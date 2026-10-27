/** 变量引用使用检查器符号绑定；不以名字/最近声明推断，不建立运行时值流。 */
export function createVariableBindings({ ts, session, result, location, compactLocation, declaration, declarationId }) {
  const objects = new WeakMap();
  const identities = new Map();
  const supported = node => (ts.isVariableDeclaration(node) || ts.isParameter(node) || ts.isBindingElement(node)) && ts.isIdentifier(node.name);
  const ownerOf = node => {
    for (let parent = node.parent; parent; parent = parent.parent) if (ts.isFunctionLike(parent)) return parent;
    return null;
  };
  const roleOf = node => {
    for (let parent = node; parent; parent = parent.parent) {
      if (ts.isParameter(parent)) return 'parameter';
      if (ts.isVariableDeclaration(parent) || ts.isFunctionLike(parent)) break;
    }
    return 'variable';
  };
  const register = node => {
    if (!supported(node) || !session.isSource(node.getSourceFile().fileName)) return null;
    if (objects.has(node)) return objects.get(node);
    const owner = ownerOf(node);
    const row = [...compactLocation(location(node)), node.name.text, roleOf(node),
      declarationId(declaration(owner)), owner ? 'callable' : 'module'];
    const key = JSON.stringify(row);
    if (!identities.has(key)) {
      identities.set(key, result.variable_declarations.length);
      result.variable_declarations.push(row);
    }
    const id = identities.get(key);
    objects.set(node, id);
    return id;
  };
  return {
    visit(node, currentOwner, checker) {
      if (supported(node)) register(node); // any/unknown 仍有词法声明身份。
      if (!ts.isIdentifier(node)) return;
      const parent = node.parent;
      if ((supported(parent) && parent.name === node) ||
          ((ts.isImportSpecifier(parent) || ts.isImportClause(parent) || ts.isNamespaceImport(parent)) && parent.name === node)) return;
      let symbol = ts.isShorthandPropertyAssignment(parent) && parent.name === node
        ? checker.getShorthandAssignmentValueSymbol(parent) : checker.getSymbolAtLocation(node);
      if (symbol?.flags & ts.SymbolFlags.Alias) symbol = checker.getAliasedSymbol(symbol);
      const candidates = (symbol?.declarations || []).filter(supported);
      if (!candidates.length) return; // 这里只声明变量能力；函数调用仍使用已有 call binding。
      const targets = [...new Set(candidates.slice(0, 16).map(register))];
      const reason = candidates.length > 16 ? 'compiler_variable_target_limit'
        : targets.includes(null) ? 'compiler_variable_declaration_outside_scope' : null;
      result.variable_references.push([...compactLocation(location(node)), node.text,
        targets.filter(id => id !== null), reason, declarationId(declaration(currentOwner)), currentOwner ? 'callable' : 'module']);
    },
  };
}
