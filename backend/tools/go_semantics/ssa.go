package main

import (
	"fmt"
	"go/ast"
	"go/token"
	"strings"

	"golang.org/x/tools/go/ssa"
)

type valueFact struct {
	Kind     string    `json:"kind"`
	Name     string    `json:"name"`
	Type     string    `json:"type_literal"`
	Location *location `json:"location,omitempty"`
}
type instructionFact struct {
	Opcode          string    `json:"opcode"`
	Block           int       `json:"block"`
	Operands        []string  `json:"operands"`
	Result          *string   `json:"result"`
	Location        *location `json:"location,omitempty"`
	CallLocation    *location `json:"call_location,omitempty"`
	SourceName      string    `json:"source_name"`
	CallArguments   []string  `json:"call_arguments"`
	ReceiverValue   *string   `json:"receiver_value"`
	InvocationKind  string    `json:"invocation_kind"`
	ProjectionIndex *int      `json:"projection_index"`
	Operator        string    `json:"operator"`
}
type blockFact struct {
	Predecessors []int    `json:"predecessors"`
	Successors   []int    `json:"successors"`
	Instructions []string `json:"instructions"`
}
type functionFact struct {
	Name              string                     `json:"name"`
	Kind              string                     `json:"kind"`
	Location          location                   `json:"location"`
	Parameters        []string                   `json:"parameters"`
	ReceiverParameter *string                    `json:"receiver_parameter"`
	Values            map[string]valueFact       `json:"values"`
	Instructions      map[string]instructionFact `json:"instructions"`
	Blocks            []blockFact                `json:"blocks"`
	Limitations       []string                   `json:"limitations"`
}

// function exports local value identity, not compiler pointers or unbounded IR strings.
func (s *session) function(fn *ssa.Function, decl declaration) functionFact {
	out := functionFact{Name: decl.Name, Kind: decl.Kind, Location: decl.Location, Parameters: []string{}, Values: map[string]valueFact{}, Instructions: map[string]instructionFact{}, Blocks: []blockFact{}, Limitations: []string{"operand_dependencies_not_memory_alias_analysis", "no_concurrency_ordering", "no_path_feasibility"}}
	ids := map[ssa.Value]string{}
	var valueID func(ssa.Value) string
	valueID = func(value ssa.Value) string {
		if id, ok := ids[value]; ok {
			return id
		}
		id := fmt.Sprintf("v%d", len(ids))
		ids[value] = id
		kind := "instruction"
		switch value.(type) {
		case *ssa.Parameter:
			kind = "parameter"
		case *ssa.Const:
			kind = "constant"
		case *ssa.Function, *ssa.Builtin:
			kind = "function"
		case *ssa.Global:
			kind = "global"
		case *ssa.FreeVar:
			kind = "free_variable"
		}
		fact := valueFact{Kind: kind, Name: value.Name(), Type: s.typeName(value.Type())}
		if value.Pos() != token.NoPos {
			loc := s.loc(value.Pos(), value.Pos())
			if loc.Path != "" {
				fact.Location = &loc
			}
		}
		out.Values[id] = fact
		return id
	}
	for index, param := range fn.Params {
		id := valueID(param)
		if index == 0 && fn.Signature.Recv() != nil {
			out.ReceiverParameter = &id
		} else {
			out.Parameters = append(out.Parameters, id)
		}
	}
	for _, block := range fn.Blocks {
		for _, inst := range block.Instrs {
			if value, ok := inst.(ssa.Value); ok {
				valueID(value)
			}
		}
	}
	for _, block := range fn.Blocks {
		b := blockFact{Predecessors: []int{}, Successors: []int{}, Instructions: []string{}}
		for _, pred := range block.Preds {
			b.Predecessors = append(b.Predecessors, pred.Index)
		}
		for _, succ := range block.Succs {
			b.Successors = append(b.Successors, succ.Index)
		}
		for index, inst := range block.Instrs {
			id := fmt.Sprintf("b%d:i%d", block.Index, index)
			fact := instructionFact{Opcode: strings.TrimPrefix(fmt.Sprintf("%T", inst), "*ssa."), Block: block.Index, Operands: []string{}, CallArguments: []string{}}
			switch operation := inst.(type) {
			case *ssa.Extract:
				index := operation.Index
				fact.ProjectionIndex = &index
			case *ssa.Field:
				index := operation.Field
				fact.ProjectionIndex = &index
			case *ssa.FieldAddr:
				index := operation.Field
				fact.ProjectionIndex = &index
			case *ssa.BinOp:
				fact.Operator = operation.Op.String()
			case *ssa.UnOp:
				fact.Operator = operation.Op.String()
			}
			for _, operand := range inst.Operands(nil) {
				if operand != nil && *operand != nil {
					fact.Operands = append(fact.Operands, valueID(*operand))
				}
			}
			if value, ok := inst.(ssa.Value); ok {
				result := valueID(value)
				fact.Result = &result
			}
			if inst.Pos() != token.NoPos {
				loc := s.loc(inst.Pos(), inst.Pos())
				if loc.Path != "" {
					fact.Location = &loc
				}
			}
			if call, ok := inst.(ssa.CallInstruction); ok {
				common := call.Common()
				fact.InvocationKind = "direct"
				args := common.Args
				if common.IsInvoke() {
					fact.InvocationKind = "invoke"
					receiver := valueID(common.Value)
					fact.ReceiverValue = &receiver
				} else if common.Signature().Recv() != nil && len(args) > 0 {
					receiver := valueID(args[0])
					fact.ReceiverValue = &receiver
					args = args[1:]
				} else if common.StaticCallee() == nil {
					fact.InvocationKind = "dynamic"
				}
				for _, arg := range args {
					fact.CallArguments = append(fact.CallArguments, valueID(arg))
				}
				if loc, ok := s.callRanges[common.Pos()]; ok {
					fact.CallLocation = &loc
				}
			}
			if ref, ok := inst.(*ssa.DebugRef); ok {
				loc := s.loc(ref.Expr.Pos(), ref.Expr.End())
				if loc.Path != "" {
					fact.Location = &loc
				}
				if ident, ok := ref.Expr.(*ast.Ident); ok {
					fact.SourceName = ident.Name
				}
			}
			out.Instructions[id] = fact
			b.Instructions = append(b.Instructions, id)
		}
		out.Blocks = append(out.Blocks, b)
	}
	return out
}
