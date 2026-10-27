package main

import (
	"fmt"
	"sort"
)

// wireResult uses the same call/type tables as TypeScript. SSA rows are an optional
// extension; public fact models stay unchanged. No paths, operands or types are dropped.
type wireResult struct {
	result
	Format       string   `json:"wire_format"`
	Declarations [][]any  `json:"declarations"`
	TypeLiterals []string `json:"type_literals"`
	Locations    [][]any  `json:"locations"`
	Calls        [][]any  `json:"calls"`
	Types        [][]any  `json:"types"`
	SSA          [][]any  `json:"ssa_functions"`
	Variables    [][]any  `json:"variable_declarations"`
	References   [][]any  `json:"variable_references"`
}

// compactResult interns immutable texts and source ranges once per request.
// Sorted local IDs make table assignment deterministic despite Go map iteration.
func compactResult(facts result) (wireResult, error) {
	out := wireResult{result: facts, Format: "tables-v1", Declarations: [][]any{},
		TypeLiterals: []string{}, Locations: [][]any{}, Calls: [][]any{}, Types: [][]any{}, SSA: [][]any{}, Variables: [][]any{}, References: [][]any{}}
	files := map[string]int{}
	for index, path := range facts.Files {
		files[path] = index
	}
	var invalid error
	coordinates := func(loc location) []any {
		index, ok := files[loc.Path]
		if !ok {
			invalid = fmt.Errorf("location outside explicit source table")
		}
		return []any{index, loc.Line, loc.Column, loc.EndLine, loc.EndColumn}
	}
	literals := map[string]int{}
	literalID := func(text string) int {
		if id, ok := literals[text]; ok {
			return id
		}
		id := len(out.TypeLiterals)
		literals[text] = id
		out.TypeLiterals = append(out.TypeLiterals, text)
		return id
	}
	declarations := map[declaration]int{}
	declarationID := func(decl declaration) int {
		if id, ok := declarations[decl]; ok {
			return id
		}
		id := len(out.Declarations)
		declarations[decl] = id
		out.Declarations = append(out.Declarations, append(coordinates(decl.Location), decl.Name, decl.Kind, decl.HasBody))
		return id
	}
	locations := map[location]int{}
	locationID := func(loc *location) *int {
		if loc == nil {
			return nil
		}
		id, ok := locations[*loc]
		if !ok {
			id = len(out.Locations)
			locations[*loc] = id
			out.Locations = append(out.Locations, coordinates(*loc))
		}
		return &id
	}
	for _, call := range facts.Calls {
		targets := []int{}
		for _, decl := range call.Declarations {
			targets = append(targets, declarationID(decl))
		}
		out.Calls = append(out.Calls, append(coordinates(call.Location), targets, call.Reason, call.Dispatch))
	}
	for _, value := range facts.Types {
		var owner *int
		if value.Owner != nil {
			id := declarationID(*value.Owner)
			owner = &id
		}
		out.Types = append(out.Types, append(coordinates(value.Location), value.Name, literalID(value.Type), value.Role, owner, value.Scope))
	}
	for _, value := range facts.Variables {
		var owner *int
		if value.Owner != nil {
			id := declarationID(*value.Owner)
			owner = &id
		}
		out.Variables = append(out.Variables, append(coordinates(value.Location), value.Name, value.Role, owner, value.Scope))
	}
	for _, value := range facts.References {
		var owner *int
		if value.Owner != nil {
			id := declarationID(*value.Owner)
			owner = &id
		}
		out.References = append(out.References, append(coordinates(value.Location), value.Name, value.Targets, value.Reason, owner, value.Scope))
	}
	for _, fn := range facts.SSA {
		fnLocation := locationID(&fn.Location)
		values := map[string][]any{}
		for _, id := range sortedKeys(fn.Values) {
			value := fn.Values[id]
			values[id] = []any{value.Kind, value.Name, literalID(value.Type), locationID(value.Location)}
		}
		instructions := map[string][]any{}
		for _, id := range sortedKeys(fn.Instructions) {
			inst := fn.Instructions[id]
			instructions[id] = []any{inst.Opcode, inst.Block, inst.Operands, inst.Result,
				locationID(inst.Location), locationID(inst.CallLocation), inst.SourceName,
				inst.CallArguments, inst.ReceiverValue, inst.InvocationKind, inst.ProjectionIndex, inst.Operator}
		}
		blocks := [][]any{}
		for _, block := range fn.Blocks {
			blocks = append(blocks, []any{block.Predecessors, block.Successors, block.Instructions})
		}
		out.SSA = append(out.SSA, []any{fn.Name, fn.Kind, fnLocation, fn.Parameters,
			fn.ReceiverParameter, values, instructions, blocks, fn.Limitations})
	}
	return out, invalid
}

func sortedKeys[T any](values map[string]T) []string {
	keys := make([]string, 0, len(values))
	for key := range values {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	return keys
}
