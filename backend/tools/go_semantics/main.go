// Command go-semantics reads compiler facts without running target code or build scripts.
package main

import (
	"context"
	"encoding/json"
	"fmt"
	"go/ast"
	"go/parser"
	"go/token"
	"go/types"
	"io"
	"os"
	"path/filepath"
	"runtime"
	"sort"
	"strings"
	"sync"
	"time"

	"golang.org/x/tools/go/packages"
	"golang.org/x/tools/go/ssa"
	"golang.org/x/tools/go/ssa/ssautil"
)

type location struct {
	Path      string `json:"path"`
	Line      int    `json:"line"`
	Column    int    `json:"column"`
	EndLine   int    `json:"end_line"`
	EndColumn int    `json:"end_column"`
}
type declaration struct {
	Location location `json:"location"`
	Name     string   `json:"name"`
	Kind     string   `json:"kind"`
	HasBody  bool     `json:"has_body"`
}
type call struct {
	Location     location      `json:"location"`
	Declarations []declaration `json:"declarations"`
	Reason       *string       `json:"unresolved_reason"`
	Dispatch     string        `json:"dispatch"`
}
type typeBinding struct {
	Location location     `json:"location"`
	Name     string       `json:"name"`
	Type     string       `json:"type_literal"`
	Role     string       `json:"role"`
	Owner    *declaration `json:"owner"`
	Scope    string       `json:"owner_scope"`
}
type request struct {
	Root      string   `json:"project_root"`
	Files     []string `json:"files"`
	MaxBytes  int64    `json:"max_file_bytes"`
	BuildTags []string `json:"build_tags"`
}
type result struct {
	Protocol     string                `json:"protocol_version"`
	Backend      string                `json:"backend"`
	Version      string                `json:"version"`
	Status       string                `json:"status"`
	Files        []string              `json:"files"`
	Calls        []call                `json:"calls"`
	Types        []typeBinding         `json:"types"`
	Variables    []variableDeclaration `json:"variable_declarations"`
	References   []variableReference   `json:"variable_references"`
	SSA          []functionFact        `json:"ssa_functions"`
	Diagnostics  []map[string]any      `json:"diagnostics"`
	Counts       map[string]int        `json:"diagnostic_counts"`
	Truncated    bool                  `json:"diagnostics_truncated"`
	Capabilities []string              `json:"capabilities"`
	Programs     int                   `json:"program_count"`
	Timings      map[string]float64    `json:"timings_ms"`
}
type session struct {
	req               request
	fset              *token.FileSet
	allowed           map[string]bool
	decls             map[*types.Func]declaration
	callRanges        map[token.Pos]location
	paths             map[string]string
	typeNames         map[types.Type]string
	pathLock          sync.Mutex
	samples           map[string]bool
	result            result
	variableObjects   map[*types.Var]int
	pendingReferences []pendingVariableReference
}

func main() {
	var req request
	if err := json.NewDecoder(io.LimitReader(os.Stdin, 16<<20)).Decode(&req); err != nil {
		os.Exit(2)
	}
	s := newSession(req)
	started := time.Now()
	s.run()
	s.finishVariableReferences()
	s.result.Timings["worker_compute"] = float64(time.Since(started).Microseconds()) / 1000
	packing := time.Now()
	wire, err := compactResult(s.result)
	if err != nil {
		os.Exit(2)
	}
	s.result.Timings["wire_pack"] = float64(time.Since(packing).Microseconds()) / 1000
	if err := json.NewEncoder(os.Stdout).Encode(wire); err != nil {
		os.Exit(2)
	}
}

func newSession(req request) *session {
	return &session{req: req, fset: token.NewFileSet(), allowed: map[string]bool{}, decls: map[*types.Func]declaration{}, callRanges: map[token.Pos]location{}, paths: map[string]string{}, typeNames: map[types.Type]string{}, samples: map[string]bool{}, result: result{Protocol: "1.0", Backend: "go", Version: "x/tools-v0.52.0", Status: "completed", Files: req.Files, Calls: []call{}, Types: []typeBinding{}, SSA: []functionFact{}, Diagnostics: []map[string]any{}, Counts: map[string]int{}, Timings: map[string]float64{}, Capabilities: []string{"declaration_binding", "go_types", "ssa_cfg", "ssa_operands", "ssa_phi", "ssa_debug_names"}}}
}

// typeName reuses exact compiler type text, never truncating it or caching across projects.
func (s *session) typeName(value types.Type) string {
	if text, ok := s.typeNames[value]; ok {
		return text
	}
	text := types.TypeString(value, nil)
	s.typeNames[value] = text
	return text
}

// diagnostic stores bounded samples and complete counts without leaking host errors.
func (s *session) diagnostic(reason, path string) {
	s.result.Status = "partial"
	s.result.Counts[reason]++
	key := reason + "\x00" + path
	if s.samples[key] {
		return
	}
	s.samples[key] = true
	if len(s.result.Diagnostics) < 100 {
		s.result.Diagnostics = append(s.result.Diagnostics, map[string]any{"reason": reason, "path": path})
	} else {
		s.result.Truncated = true
	}
}
func (s *session) relative(path string) (string, bool) {
	s.pathLock.Lock()
	defer s.pathLock.Unlock()
	if rel, ok := s.paths[path]; ok {
		return rel, rel != ""
	}
	resolved, err := filepath.EvalSymlinks(path)
	if err != nil {
		s.paths[path] = ""
		return "", false
	}
	rel, err := filepath.Rel(s.req.Root, resolved)
	if err != nil || rel == ".." || strings.HasPrefix(rel, ".."+string(filepath.Separator)) || filepath.IsAbs(rel) {
		s.paths[path] = ""
		return "", false
	}
	s.paths[path] = filepath.ToSlash(rel)
	return s.paths[path], true
}
func (s *session) loc(start, end token.Pos) location {
	a, b := s.fset.PositionFor(start, false), s.fset.PositionFor(end, false)
	rel, ok := s.relative(a.Filename)
	if !ok {
		return location{}
	}
	return location{rel, a.Line, a.Column, b.Line, b.Column}
}
func (s *session) parse(fset *token.FileSet, filename string, src []byte) (*ast.File, error) {
	rel, ok := s.relative(filename)
	if !ok || !s.allowed[rel] {
		return nil, fmt.Errorf("source outside explicit scope")
	}
	info, err := os.Stat(filename)
	if err != nil || info.Size() > s.req.MaxBytes {
		return nil, fmt.Errorf("source limit")
	}
	return parser.ParseFile(fset, filename, src, parser.ParseComments|parser.AllErrors)
}

// run loads only explicitly selected package directories; dependencies use export data.
func (s *session) run() {
	root, err := filepath.EvalSymlinks(s.req.Root)
	if err != nil {
		s.diagnostic("project_root_unavailable", "")
		return
	}
	s.req.Root = root
	groups := map[string]map[string]bool{}
	for _, rel := range s.req.Files {
		path := filepath.Join(root, filepath.FromSlash(rel))
		canonical, ok := s.relative(path)
		if !ok || canonical != rel {
			s.diagnostic("source_outside_scope", rel)
			continue
		}
		s.allowed[rel] = true
		dir := filepath.Dir(path)
		module := ""
		for current := dir; ; current = filepath.Dir(current) {
			if _, err := os.Stat(filepath.Join(current, "go.mod")); err == nil {
				module = current
				break
			}
			if current == root || filepath.Dir(current) == current {
				break
			}
		}
		if module == "" {
			s.diagnostic("go_module_not_found", rel)
			continue
		}
		if groups[module] == nil {
			groups[module] = map[string]bool{}
		}
		pattern, _ := filepath.Rel(module, dir)
		if pattern == "." {
			groups[module]["."] = true
		} else {
			groups[module]["./"+filepath.ToSlash(pattern)] = true
		}
	}
	modules := make([]string, 0, len(groups))
	for module := range groups {
		modules = append(modules, module)
	}
	sort.Strings(modules)
	seen := map[string]bool{}
	for _, module := range modules {
		patterns := []string{}
		for pattern := range groups[module] {
			patterns = append(patterns, pattern)
		}
		sort.Strings(patterns)
		ctx, cancel := context.WithTimeout(context.Background(), 100*time.Second)
		flags := []string{"-mod=readonly"}
		if len(s.req.BuildTags) > 0 {
			flags = append(flags, "-tags="+strings.Join(s.req.BuildTags, ","))
		}
		config := &packages.Config{Context: ctx, Dir: module, Mode: packages.LoadSyntax, Tests: false, Fset: s.fset, ParseFile: s.parse, Env: os.Environ(), BuildFlags: flags}
		started := time.Now()
		pkgs, loadErr := packages.Load(config, patterns...)
		cancel()
		s.result.Timings["package_load"] += float64(time.Since(started).Microseconds()) / 1000
		if loadErr != nil {
			s.diagnostic("go_package_load_failed", "")
			continue
		}
		s.result.Programs += len(pkgs)
		packages.Visit(pkgs, nil, func(pkg *packages.Package) {
			for _, problem := range pkg.Errors {
				s.diagnostic(packageErrorReason(problem.Msg), pkg.PkgPath)
			}
		})
		for _, pkg := range pkgs {
			if len(pkg.Errors) > 0 || pkg.IllTyped {
				s.diagnostic("go_package_type_errors", pkg.PkgPath)
			}
			for _, file := range pkg.Syntax {
				rel, _ := s.relative(s.fset.Position(file.Pos()).Filename)
				seen[rel] = true
				s.collectDeclarations(pkg, file)
			}
		}
		for _, pkg := range pkgs {
			if pkg.IllTyped {
				continue
			}
			for _, file := range pkg.Syntax {
				s.collectFacts(pkg, file)
			}
		}
		s.buildSSA(pkgs)
	}
	for _, path := range s.req.Files {
		if !seen[path] {
			s.diagnostic("go_file_not_loaded", path)
		}
	}
	// This is a compilation configuration, not a claim of cross-platform coverage.
	goos, goarch := os.Getenv("GOOS"), os.Getenv("GOARCH")
	if goos == "" {
		goos = runtime.GOOS
	}
	if goarch == "" {
		goarch = runtime.GOARCH
	}
	s.result.Diagnostics = append(s.result.Diagnostics, map[string]any{"reason": "go_build_context", "goos": goos, "goarch": goarch, "host_goos": runtime.GOOS, "host_goarch": runtime.GOARCH, "build_tags": strings.Join(s.req.BuildTags, ",")})
}

// packageErrorReason separates environment failures without publishing host paths or raw errors.
func packageErrorReason(message string) string {
	message = strings.ToLower(message)
	for reason, fragments := range map[string][]string{
		"go_filesystem_access_denied":   {"access is denied", "permission denied"},
		"go_dependency_unavailable":     {"lookup disabled", "cannot find module", "missing go.sum", "no required module"},
		"go_build_constraints_excluded": {"build constraints exclude", "no go files"},
	} {
		for _, fragment := range fragments {
			if strings.Contains(message, fragment) {
				return reason
			}
		}
	}
	return "go_type_or_load_error"
}

func (s *session) collectDeclarations(pkg *packages.Package, file *ast.File) {
	for _, node := range file.Decls {
		if fn, ok := node.(*ast.FuncDecl); ok {
			object, ok := pkg.TypesInfo.Defs[fn.Name].(*types.Func)
			if !ok {
				continue
			}
			kind := "function"
			if fn.Recv != nil {
				kind = "method"
			}
			s.decls[object] = declaration{s.loc(fn.Pos(), fn.End()), fn.Name.Name, kind, fn.Body != nil}
		}
	}
}
func (s *session) collectFacts(pkg *packages.Package, file *ast.File) {
	parameterNames := map[token.Pos]bool{}
	markParameters := func(fn *ast.FuncType) {
		for _, field := range fn.Params.List {
			for _, name := range field.Names {
				parameterNames[name.Pos()] = true
			}
		}
	}
	var visit func(ast.Node, *declaration)
	visit = func(node ast.Node, owner *declaration) {
		if node == nil {
			return
		}
		ast.Inspect(node, func(child ast.Node) bool {
			if child == nil {
				return false
			}
			if fn, ok := child.(*ast.FuncDecl); ok {
				obj, _ := pkg.TypesInfo.Defs[fn.Name].(*types.Func)
				d := s.decls[obj]
				markParameters(fn.Type)
				if obj != nil {
					sig := obj.Type().(*types.Signature)
					s.result.Types = append(s.result.Types, typeBinding{d.Location, d.Name, s.typeName(sig.Results()), "return", &d, "callable"})
				}
				visit(fn.Type, &d)
				if fn.Recv != nil {
					visit(fn.Recv, &d)
				}
				if fn.Body != nil {
					visit(fn.Body, &d)
				}
				return false
			}
			// The shared syntax frontend uses the same exact source-position identity.
			if fn, ok := child.(*ast.FuncLit); ok {
				markParameters(fn.Type)
				owner := s.anonymousDeclaration(fn)
				visit(fn.Type, &owner)
				visit(fn.Body, &owner)
				return false
			}
			if ident, ok := child.(*ast.Ident); ok {
				if variable, ok := pkg.TypesInfo.Defs[ident].(*types.Var); ok && ident.Name != "_" && !variable.IsField() {
					role := "variable"
					if parameterNames[ident.Pos()] {
						role = "parameter"
					}
					scope := "module"
					if owner != nil {
						scope = "callable"
					}
					s.result.Types = append(s.result.Types, typeBinding{s.loc(ident.Pos(), ident.End()), ident.Name, s.typeName(variable.Type()), role, owner, scope})
					s.registerVariable(variable, variableDeclaration{s.loc(ident.Pos(), ident.End()), ident.Name, role, owner, scope})
				}
				if variable, ok := pkg.TypesInfo.Uses[ident].(*types.Var); ok && !variable.IsField() {
					scope := "module"
					if owner != nil {
						scope = "callable"
					}
					s.pendingReferences = append(s.pendingReferences, pendingVariableReference{variable, s.loc(ident.Pos(), ident.End()), ident.Name, owner, scope})
				}
			}
			if expr, ok := child.(*ast.CallExpr); ok {
				s.collectCall(pkg, expr)
			}
			return true
		})
	}
	visit(file, nil)
}
func (s *session) collectCall(pkg *packages.Package, expr *ast.CallExpr) {
	loc := s.loc(expr.Pos(), expr.End())
	s.callRanges[expr.Lparen] = loc
	binding := call{Location: loc, Declarations: []declaration{}, Dispatch: "direct"}
	reason := "go_call_target_unresolved"
	fun := expr.Fun
	for {
		switch value := fun.(type) {
		case *ast.IndexExpr:
			fun = value.X
		case *ast.IndexListExpr:
			fun = value.X
		case *ast.ParenExpr:
			fun = value.X
		default:
			goto unwrapped
		}
	}
unwrapped:
	var object types.Object
	switch target := fun.(type) {
	case *ast.Ident:
		object = pkg.TypesInfo.Uses[target]
	case *ast.SelectorExpr:
		object = pkg.TypesInfo.Uses[target.Sel]
		if selection := pkg.TypesInfo.Selections[target]; selection != nil {
			if _, ok := selection.Recv().Underlying().(*types.Interface); ok {
				binding.Dispatch = "virtual"
				reason = "go_interface_dispatch"
				binding.Reason = &reason
				s.result.Calls = append(s.result.Calls, binding)
				return
			}
		}
	}
	if object, ok := object.(*types.Func); ok {
		if d, ok := s.decls[object.Origin()]; ok {
			binding.Declarations = append(binding.Declarations, d)
		} else {
			reason = "go_external_declaration"
		}
	}
	if len(binding.Declarations) == 0 {
		binding.Reason = &reason
	}
	s.result.Calls = append(s.result.Calls, binding)
}

// buildSSA excludes ill-typed packages instead of presenting guessed SSA as native facts.
func (s *session) buildSSA(pkgs []*packages.Package) {
	started := time.Now()
	defer func() {
		s.result.Timings["ssa_build_extract"] += float64(time.Since(started).Microseconds()) / 1000
		if recover() != nil {
			s.diagnostic("go_ssa_build_failed", "")
		}
	}()
	prog, built := ssautil.Packages(pkgs, ssa.GlobalDebug|ssa.InstantiateGenerics)
	for index, pkg := range built {
		if pkg == nil {
			s.diagnostic("go_ssa_package_unavailable", pkgs[index].PkgPath)
			continue
		}
		pkg.Build()
		for _, file := range pkgs[index].Syntax {
			for _, node := range file.Decls {
				if decl, ok := node.(*ast.FuncDecl); ok && decl.Body != nil {
					obj, _ := pkgs[index].TypesInfo.Defs[decl.Name].(*types.Func)
					if fn := prog.FuncValue(obj); fn != nil && len(fn.Blocks) > 0 {
						s.result.SSA = append(s.result.SSA, s.function(fn, s.decls[obj]))
						s.collectAnonymousSSA(fn)
					}
				}
			}
		}
	}
}

// Anonymous identities are source positions, not unstable compiler-generated names.
func (s *session) anonymousDeclaration(fn *ast.FuncLit) declaration {
	loc := s.loc(fn.Pos(), fn.End())
	return declaration{Location: loc, Name: fmt.Sprintf("anonymous@%d:%d", loc.Line, loc.Column), Kind: "function", HasBody: true}
}

func (s *session) collectAnonymousSSA(parent *ssa.Function) {
	for _, child := range parent.AnonFuncs {
		if syntax, ok := child.Syntax().(*ast.FuncLit); ok && len(child.Blocks) > 0 {
			s.result.SSA = append(s.result.SSA, s.function(child, s.anonymousDeclaration(syntax)))
		}
		s.collectAnonymousSSA(child)
	}
}
