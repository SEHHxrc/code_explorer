/** 一次 stdin 请求、一次 JSON 响应；仅输出语义事实，不加载被分析项目代码。 */
import fs from 'node:fs';
import { createSession, ts } from './project.mjs';
import { createVariableBindings } from './references.mjs';

const request = JSON.parse(fs.readFileSync(0, 'utf8'));
const started = performance.now();
if (request.protocol_version !== '1.0' || ts.version !== '6.0.3') throw new Error('unsupported_protocol_or_compiler');
const result = {
  protocol_version: '1.0', backend: 'typescript', version: ts.version, status: 'completed',
  files: request.files, calls: [], types: [], declarations: [], type_literals: [], diagnostics: [],
  wire_format: 'tables-v1',
  diagnostic_counts: {}, diagnostics_truncated: false, program_count: 0,
  timings_ms: {}, cache_stats: {},
  variable_declarations: [], variable_references: [],
  capabilities: ['declaration_binding', 'symbol_alias_resolution', 'type_information', 'module_isolation_default', 'variable_reference_binding'],
};
const session = createSession(request, result.diagnostics, result.diagnostic_counts);
result.timings_ms.session_setup = performance.now() - started;
result.program_count = session.groups.size;
const fileIds = new Map(request.files.map((file, index) => [file, index]));
const declarationIds = new Map();
const declarationObjects = new WeakMap();
const typeIds = new Map();
const locations = new WeakMap();
const declarationCache = new WeakMap();
const cacheStats = { location_hits: 0, declaration_hits: 0, declaration_id_hits: 0 };

/** 内部传输用共享表，不为每个调用/形参反复复制路径、声明范围和长类型文本。 */
function compactLocation(item) {
  return [fileIds.get(item.path), item.line, item.column, item.end_line, item.end_column];
}
function declarationId(item) {
  if (!item) return null;
  if (declarationObjects.has(item)) { cacheStats.declaration_id_hits += 1; return declarationObjects.get(item); }
  const key = JSON.stringify(item);
  if (!declarationIds.has(key)) {
    declarationIds.set(key, result.declarations.length);
    result.declarations.push([...compactLocation(item.location), item.name, item.kind, item.has_body]);
  }
  const id = declarationIds.get(key);
  declarationObjects.set(item, id);
  return id;
}
function typeId(text) {
  if (!typeIds.has(text)) { typeIds.set(text, result.type_literals.length); result.type_literals.push(text); }
  return typeIds.get(text);
}

/** TypeScript 使用 UTF-16，公共协议使用一基 UTF-8 字节列；结束位置保持开区间。 */
function location(node) {
  if (locations.has(node)) { cacheStats.location_hits += 1; return locations.get(node); }
  const source = node.getSourceFile();
  const point = offset => {
    const position = source.getLineAndCharacterOfPosition(offset);
    const lineStart = source.getPositionOfLineAndCharacter(position.line, 0);
    return [position.line + 1, Buffer.byteLength(source.text.slice(lineStart, offset), 'utf8') + 1];
  };
  const [line, column] = point(node.getStart(source));
  const [end_line, end_column] = point(node.end);
  const value = { path: session.relative(source.fileName), line, column, end_line, end_column };
  locations.set(node, value);
  return value;
}

function declaration(node) {
  if (!node) return null;
  if (declarationCache.has(node)) { cacheStats.declaration_hits += 1; return declarationCache.get(node); }
  const value = buildDeclaration(node);
  declarationCache.set(node, value); // 包括 null；外部库声明不需要逐调用重复检查。
  return value;
}

function buildDeclaration(node) {
  if (!node || !session.isSource(node.getSourceFile().fileName)) return null;
  if (ts.isVariableDeclaration(node) && node.initializer &&
      (ts.isArrowFunction(node.initializer) || ts.isFunctionExpression(node.initializer))) {
    return { location: location(node), name: node.name.getText(), kind: 'function', has_body: true };
  }
  let kind = 'signature';
  if (ts.isFunctionDeclaration(node) || ts.isFunctionExpression(node) || ts.isArrowFunction(node)) kind = 'function';
  else if (ts.isMethodDeclaration(node) || ts.isMethodSignature(node)) kind = 'method';
  else if (ts.isConstructorDeclaration(node)) kind = 'constructor';
  else if (ts.isClassDeclaration(node) || ts.isClassExpression(node)) kind = 'class';
  let name = ts.isConstructorDeclaration(node) ? 'constructor' : node.name?.getText() || '';
  if ((ts.isArrowFunction(node) || ts.isFunctionExpression(node)) && ts.isVariableDeclaration(node.parent)) {
    return declaration(node.parent);
  }
  if (!name) return null; // 匿名函数的身份由现有语法前端拥有，本批不猜测。
  return { location: location(node), name, kind, has_body: Boolean(node.body) || kind === 'class' };
}

/** 类型系统可能合并签名相同的条件分支；仅展开 const 的显式声明别名，不推测可变赋值。 */
function declarationsFor(expression, checker, seen = new Set(), depth = 0) {
  if (!expression || depth > 4 || seen.has(expression)) return [];
  seen.add(expression);
  if (ts.isParenthesizedExpression(expression)) return declarationsFor(expression.expression, checker, seen, depth + 1);
  if (ts.isConditionalExpression(expression)) return [expression.whenTrue, expression.whenFalse]
    .flatMap(item => declarationsFor(item, checker, seen, depth + 1));
  const member = ts.isPropertyAccessExpression(expression) || ts.isElementAccessExpression(expression);
  let symbol = checker.getSymbolAtLocation(member ? expression.name || expression.argumentExpression : expression);
  if (symbol?.flags & ts.SymbolFlags.Alias) symbol = checker.getAliasedSymbol(symbol);
  const declarations = symbol?.declarations || [];
  return declarations.flatMap(item => {
    if (ts.isVariableDeclaration(item) && item.initializer && item.parent.flags & ts.NodeFlags.Const &&
        (ts.isIdentifier(item.initializer) || ts.isConditionalExpression(item.initializer) || ts.isParenthesizedExpression(item.initializer))) {
      return declarationsFor(item.initializer, checker, seen, depth + 1);
    }
    return [item];
  });
}

const variableBindings = createVariableBindings({ ts, session, result, location, compactLocation, declaration, declarationId });
for (const group of session.groups.values()) {
  const programStarted = performance.now();
  const program = session.programFor(group);
  const checker = program.getTypeChecker();
  for (const error of program.getOptionsDiagnostics()) session.diagnostic('compiler_options_error', '', error.code);
  result.timings_ms.program_setup = (result.timings_ms.program_setup || 0) + performance.now() - programStarted;
  const extractionStarted = performance.now();
  for (const file of group.files) {
    const source = program.getSourceFile(file);
    if (!source) { session.diagnostic('compiler_source_unavailable', session.relative(file)); continue; }
    const syntaxErrors = program.getSyntacticDiagnostics(source);
    for (const error of syntaxErrors) session.diagnostic('compiler_syntax_error', session.relative(file), error.code);
    // 收集必要的类型/模块错误，不触发逐文件完整安全分析或执行项目。
    let currentOwner = null;
    const recordType = (node, name, role, type) => {
      if (type.flags & (ts.TypeFlags.Any | ts.TypeFlags.Unknown)) return;
      const text = checker.typeToString(type, node, ts.TypeFormatFlags.NoTruncation);
      const owner = role === 'return' ? node : currentOwner;
      if (text.length <= 4096) result.types.push([...compactLocation(location(node)), name, typeId(text), role,
        declarationId(declaration(owner)), owner ? 'callable' : 'module']);
      else session.diagnostic('compiler_type_text_limit', session.relative(file));
    };
    function visit(node) {
      const previousOwner = currentOwner;
      if (ts.isFunctionLike(node)) currentOwner = node;
      variableBindings.visit(node, currentOwner, checker);
      if (ts.isCallExpression(node) || ts.isNewExpression(node)) {
        const binding = { location: location(node), declarations: [], dispatch: 'direct' };
        const expression = node.expression;
        const type = checker.getTypeAtLocation(expression);
        const kind = ts.isNewExpression(node) ? ts.SignatureKind.Construct : ts.SignatureKind.Call;
        const variants = type.isUnion() ? type.types : [type];
        const signatures = variants.flatMap(item => checker.getSignaturesOfType(item, kind));
        const resolved = checker.getResolvedSignature(node);
        const signatureDeclarations = [...signatures.map(item => item.getDeclaration()),
          ...(type.isUnion() ? [] : [resolved?.getDeclaration()])];
        const isMember = ts.isPropertyAccessExpression(expression) || ts.isElementAccessExpression(expression);
        if (isMember) binding.dispatch = 'virtual';
        const declarations = [...signatureDeclarations, ...declarationsFor(expression, checker)];
        const unique = new Map();
        for (const item of declarations) {
          const target = declaration(item);
          if (target) unique.set(JSON.stringify(target), target);
        }
        binding.declarations = [...unique.values()].slice(0, 16);
        if (unique.size > 16) binding.unresolved_reason = 'compiler_target_limit';
        else if (!binding.declarations.length) binding.unresolved_reason =
          type.flags & (ts.TypeFlags.Any | ts.TypeFlags.Unknown) ? 'compiler_unknown_callable' : 'compiler_external_or_unmapped_declaration';
        if (binding.unresolved_reason) session.diagnostic(binding.unresolved_reason, session.relative(file));
        result.calls.push([...compactLocation(binding.location), binding.declarations.map(declarationId),
          binding.unresolved_reason || null, binding.dispatch]);
      }
      if ((ts.isVariableDeclaration(node) || ts.isParameter(node)) && ts.isIdentifier(node.name)) {
        recordType(node, node.name.text, ts.isParameter(node) ? 'parameter' : 'variable', checker.getTypeAtLocation(node));
      }
      if (ts.isFunctionLike(node) && node.body) {
        const target = declaration(node);
        const signature = checker.getSignatureFromDeclaration(node);
        if (target && signature) recordType(node, target.name, 'return', checker.getReturnTypeOfSignature(signature));
      }
      ts.forEachChild(node, visit);
      currentOwner = previousOwner;
    }
    visit(source);
  }
  result.timings_ms.fact_extraction = (result.timings_ms.fact_extraction || 0) + performance.now() - extractionStarted;
}
if (Object.keys(result.diagnostic_counts).some(reason => reason !== 'compiler_config_loaded')) result.status = 'partial';
result.diagnostics_truncated = session.diagnosticsOmitted() > 0;
result.timings_ms.worker_compute = performance.now() - started;
result.cache_stats = { ...session.cacheStats, ...cacheStats };
process.stdout.write(JSON.stringify(result));
