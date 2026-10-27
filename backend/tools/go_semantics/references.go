package main

import "go/types"

// Variable declarations are source entities, not runtime allocations or SSA versions.
type variableDeclaration struct {
	Location location
	Name     string
	Role     string
	Owner    *declaration
	Scope    string
}

type variableReference struct {
	Location location
	Name     string
	Targets  []int
	Reason   *string
	Owner    *declaration
	Scope    string
}

type pendingVariableReference struct {
	Object   *types.Var
	Location location
	Name     string
	Owner    *declaration
	Scope    string
}

func (s *session) registerVariable(object *types.Var, fact variableDeclaration) {
	if s.variableObjects == nil {
		s.variableObjects = map[*types.Var]int{}
	}
	if _, exists := s.variableObjects[object]; exists {
		return
	}
	s.variableObjects[object] = len(s.result.Variables)
	s.result.Variables = append(s.result.Variables, fact)
}

// Delaying lookup allows forward and cross-file references without a second AST walk.
// Uses contains both reads and writes: these bindings must not become DFG edges directly.
func (s *session) finishVariableReferences() {
	for _, pending := range s.pendingReferences {
		targets := []int{}
		var reason *string
		if id, exists := s.variableObjects[pending.Object]; exists {
			targets = append(targets, id)
		} else {
			value := "compiler_variable_declaration_outside_scope"
			reason = &value
		}
		s.result.References = append(s.result.References, variableReference{pending.Location, pending.Name, targets, reason, pending.Owner, pending.Scope})
	}
	s.result.Capabilities = append(s.result.Capabilities, "variable_reference_binding")
	s.pendingReferences = nil
}
