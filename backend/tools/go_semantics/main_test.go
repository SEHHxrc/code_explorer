package main

import (
	"bytes"
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
)

// TestCompactResult guards the wire schema, shared IDs and deterministic emission.
// Native integration tests in Python additionally check all restored public facts.
func TestCompactResult(t *testing.T) {
	loc := location{"main.go", 1, 1, 5, 2}
	decl := declaration{loc, "Run", "function", true}
	input := result{Files: []string{"main.go"},
		Calls: []call{{Location: loc, Declarations: []declaration{decl}}, {Location: loc, Declarations: []declaration{decl}}},
		Types: []typeBinding{{loc, "input", "string", "parameter", &decl, "callable"}},
		SSA: []functionFact{{Name: "Run", Kind: "function", Location: loc,
			Values: map[string]valueFact{"v1": {Kind: "parameter", Type: "string", Location: &loc},
				"v0": {Kind: "constant", Type: "string"}}, Instructions: map[string]instructionFact{}, Blocks: []blockFact{}}}}
	var previous []byte
	for range 10 {
		wire, err := compactResult(input)
		if err != nil {
			t.Fatal(err)
		}
		if len(wire.Declarations) != 1 || len(wire.TypeLiterals) != 1 || len(wire.Locations) != 1 {
			t.Fatal("shared tables were not deduplicated")
		}
		body, err := json.Marshal(wire)
		if err != nil {
			t.Fatal(err)
		}
		if previous != nil && !bytes.Equal(previous, body) {
			t.Fatal("unstable table assignment")
		}
		previous = body
		var payload map[string]json.RawMessage
		if err := json.Unmarshal(body, &payload); err != nil || string(payload["wire_format"]) != `"tables-v1"` {
			t.Fatal("invalid wire format")
		}
		var calls [][]any
		if err := json.Unmarshal(payload["calls"], &calls); err != nil || len(calls[0]) != 8 {
			t.Fatal("expanded calls shadow compact rows")
		}
	}
	input.Files = nil
	if _, err := compactResult(input); err == nil {
		t.Fatal("source outside file table accepted")
	}
}

func TestPackageErrorClassification(t *testing.T) {
	for _, sample := range []struct{ message, want string }{
		{"mkdir cache: Access is denied.", "go_filesystem_access_denied"},
		{"open cache: permission denied", "go_filesystem_access_denied"},
		{"module lookup disabled by GOPROXY=off", "go_dependency_unavailable"},
		{"build constraints exclude all Go files", "go_build_constraints_excluded"},
		{"undefined: value", "go_type_or_load_error"},
	} {
		if got := packageErrorReason(sample.message); got != sample.want {
			t.Errorf("got %s, want %s", got, sample.want)
		}
	}
}

// TestNativeSSA validates the trusted extractor without running the fixture program.
func TestNativeSSA(t *testing.T) {
	for _, pair := range []struct{ name, source string }{
		{"phi", "package sample\nfunc Flow(input string, flag bool) string { value:=input; if flag { value=\"safe\" }; return value }\n"},
		{"overwrite", "package sample\nfunc Kill(input string) string { input=\"safe\"; return input }\n"},
		{"interface", "package sample\ntype Worker interface {Run(string) string}\nfunc Entry(w Worker, x string) string {return w.Run(x)}\n"},
	} {
		t.Run(pair.name, func(t *testing.T) {
			for key, value := range map[string]string{"GOPACKAGESDRIVER": "off", "GOWORK": "off", "GOENV": "off", "GOFLAGS": "-mod=readonly", "GOPROXY": "off", "GOTOOLCHAIN": "local", "CGO_ENABLED": "0"} {
				t.Setenv(key, value)
			}
			root := t.TempDir()
			if err := os.WriteFile(filepath.Join(root, "go.mod"), []byte("module example.test/sample\n\ngo 1.23.0\n"), 0600); err != nil {
				t.Fatal(err)
			}
			if err := os.WriteFile(filepath.Join(root, "main.go"), []byte(pair.source), 0600); err != nil {
				t.Fatal(err)
			}
			s := newSession(request{Root: root, Files: []string{"main.go"}, MaxBytes: 100000})
			s.run()
			if s.result.Status != "completed" || len(s.result.SSA) != 1 {
				t.Fatalf("incomplete SSA: %+v", s.result.Diagnostics)
			}
			fn := s.result.SSA[0]
			found := false
			for _, inst := range fn.Instructions {
				switch pair.name {
				case "phi":
					if inst.Opcode == "Phi" {
						found = true
						if len(inst.Operands) != len(fn.Blocks[inst.Block].Predecessors) {
							t.Fatal("phi predecessors lost")
						}
					}
				case "overwrite":
					if inst.Opcode == "Return" {
						found = true
						if fn.Values[inst.Operands[0]].Kind != "constant" {
							t.Fatal("old input retained")
						}
					}
				case "interface":
					if inst.InvocationKind == "invoke" {
						found = true
						if len(inst.CallArguments) != 1 || inst.ReceiverValue == nil || inst.CallLocation == nil {
							t.Fatal("receiver/argument/callsite lost")
						}
					}
				}
			}
			if !found {
				t.Fatal("required SSA operation not found")
			}
			if _, err := json.Marshal(s.result); err != nil {
				t.Fatal(err)
			}
		})
	}
}
