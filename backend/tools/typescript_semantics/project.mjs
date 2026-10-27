/** 创建只读编译环境：源码严格使用调用方清单，配置与声明文件不得越过根目录。 */
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
export const ts = require('./node_modules/typescript/lib/typescript.js');
const libraryRoot = path.dirname(require.resolve('./node_modules/typescript/lib/typescript.js'));

export function createSession(request, diagnostics, counts) {
  const root = fs.realpathSync.native(request.project_root);
  const limit = request.max_file_bytes;
  const sources = new Set();
  const reads = new Map();
  const pathCache = new Map();
  const directoryCache = new Map();
  const configDirectories = new Map();
  const relativePaths = new Map();
  const cacheStats = { canonical_path_hits: 0, directory_hits: 0, config_directory_hits: 0, read_hits: 0 };
  const reported = new Set();
  let omittedDiagnostics = 0;
  const within = (base, file) => {
    const relative = path.relative(base, file);
    return relative === '' || (!relative.startsWith(`..${path.sep}`) && relative !== '..' && !path.isAbsolute(relative));
  };
  const relative = file => {
    if (!relativePaths.has(file)) relativePaths.set(file, path.relative(root, file).split(path.sep).join('/'));
    return relativePaths.get(file);
  };
  function diagnostic(reason, file = '', code = 0) {
    counts[reason] = (counts[reason] || 0) + 1;
    const key = `${reason}:${file}:${code}`;
    if (!reported.has(key)) {
      reported.add(key);
      if (diagnostics.length < 200) diagnostics.push({ reason, ...(file ? { path: file } : {}), ...(code ? { code } : {}) });
      else omittedDiagnostics += 1;
    }
  }
  function safePath(file) {
    file = path.resolve(file);
    if (pathCache.has(file)) { cacheStats.canonical_path_hits += 1; return pathCache.get(file); }
    let allowed = null;
    try {
      // 使用操作系统规范化入口，避免 JS realpath 逐级 lstat 同一批长路径。
      const canonical = fs.realpathSync.native(file);
      if (within(root, canonical) || within(libraryRoot, canonical)) allowed = canonical;
    } catch { /* 明确的任务级负缓存，不在上传后重用。 */ }
    pathCache.set(file, allowed);
    return allowed;
  }
  for (const file of request.files) {
    const absolute = path.resolve(root, file);
    const canonical = safePath(absolute);
    if (canonical && within(root, absolute) && within(root, canonical)) sources.add(absolute);
    else diagnostic('source_path_unavailable', file);
  }
  function readFile(file) {
    file = path.resolve(file);
    if (reads.has(file)) { cacheStats.read_hits += 1; return reads.get(file); }
    const canonical = safePath(file);
    if (!canonical || !(sources.has(file) || file.endsWith('.json') || /\.d\.[cm]?ts$/i.test(file))) {
      reads.set(file, undefined);
      return undefined;
    }
    try {
      if (fs.statSync(canonical).size > (within(libraryRoot, canonical) ? 16 * 1024 * 1024 : limit)) {
        diagnostic('semantic_file_too_large', within(root, file) ? relative(file) : 'typescript-lib');
        return undefined;
      }
      const text = fs.readFileSync(canonical, 'utf8');
      reads.set(file, text);
      return text;
    } catch { return undefined; }
  }
  const fileExists = file => readFile(file) !== undefined;
  const directoryExists = file => {
    file = path.resolve(file);
    if (directoryCache.has(file)) { cacheStats.directory_hits += 1; return directoryCache.get(file); }
    const canonical = safePath(file);
    const exists = canonical !== null && fs.statSync(canonical).isDirectory();
    directoryCache.set(file, exists);
    return exists;
  };
  const configHost = {
    useCaseSensitiveFileNames: ts.sys.useCaseSensitiveFileNames,
    readFile, fileExists, readDirectory: () => [],
  };
  const configs = new Map();
  function optionsFor(file) {
    let directory = path.dirname(file);
    let config = '';
    const visited = [];
    while (within(root, directory)) {
      if (configDirectories.has(directory)) {
        cacheStats.config_directory_hits += 1;
        config = configDirectories.get(directory);
        break;
      }
      visited.push(directory);
      config = ['tsconfig.json', 'jsconfig.json'].map(name => path.join(directory, name)).find(fileExists) || '';
      if (config || directory === root) break;
      directory = path.dirname(directory);
    }
    for (const item of visited) configDirectories.set(item, config);
    if (!configs.has(config)) {
      let options = {};
      if (config) {
        const loaded = ts.readConfigFile(config, readFile);
        if (loaded.error) diagnostic('compiler_config_error', relative(config), loaded.error.code);
        const parsed = ts.parseJsonConfigFileContent(loaded.config || {}, configHost, path.dirname(config), {}, config);
        for (const error of parsed.errors) {
          if (error.code !== 18003) diagnostic('compiler_config_error', relative(config), error.code);
        }
        options = parsed.options;
        const digest = crypto.createHash('sha256').update(JSON.stringify(options)).digest('hex');
        diagnostic('compiler_config_loaded', relative(config));
        if (diagnostics.length < 200) diagnostics.push({ reason: 'compiler_options_sha256', path: relative(config), sha256: digest });
      }
      // 不 emit、不运行插件/构建；不读取项目 references 的构建产物。
      configs.set(config, {
        target: ts.ScriptTarget.ESNext, module: ts.ModuleKind.NodeNext,
        moduleResolution: ts.ModuleResolutionKind.NodeNext,
        moduleDetection: ts.ModuleDetectionKind.Force,
        ...options, allowJs: true, checkJs: true, noEmit: true,
        skipLibCheck: true, incremental: false, composite: false,
        disableSourceOfProjectReferenceRedirect: true,
      });
    }
    return { config, options: configs.get(config) };
  }
  const groups = new Map();
  for (const file of sources) {
    const { config, options } = optionsFor(file);
    if (!groups.has(config)) groups.set(config, { options, files: [] });
    groups.get(config).files.push(file);
  }
  function programFor(group) {
    const host = ts.createCompilerHost(group.options, true);
    host.readFile = readFile;
    host.fileExists = fileExists;
    host.directoryExists = directoryExists;
    host.getCurrentDirectory = () => root;
    host.realpath = file => safePath(file) || file;
    host.getSourceFile = (file, languageVersion) => {
      const source = readFile(file);
      return source === undefined ? undefined : ts.createSourceFile(file, source, languageVersion, true);
    };
    host.writeFile = () => { throw new Error('emit_disabled'); };
    return ts.createProgram({ rootNames: group.files, options: group.options, host });
  }
  const sourceNames = new Map();
  const isSource = file => {
    if (!sourceNames.has(file)) sourceNames.set(file, sources.has(path.resolve(file)));
    return sourceNames.get(file);
  };
  return { relative, groups, programFor, diagnostic, isSource, cacheStats, diagnosticsOmitted: () => omittedDiagnostics };
}
