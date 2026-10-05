module hcl-test-suite/adapters/go

go 1.26.4

require (
	github.com/hashicorp/hcl/v2 v2.24.0
	github.com/kmoneil/tenon v0.17.0
	github.com/kmoneil/tenon/ctytenon v0.3.0
	github.com/zclconf/go-cty v1.19.0
)

require (
	github.com/agext/levenshtein v1.2.1 // indirect
	github.com/apparentlymart/go-textseg/v15 v15.0.0 // indirect
	github.com/apparentlymart/go-textseg/v17 v17.0.1 // indirect
	github.com/mitchellh/go-wordwrap v1.0.1 // indirect
	golang.org/x/mod v0.41.0 // indirect
	golang.org/x/sync v0.23.0 // indirect
	golang.org/x/text v0.42.0 // indirect
	golang.org/x/tools v0.49.0 // indirect
)

// Builds the adapter with the operators of tenon v0.17.0 in place of go-cty's,
// crossing values with its bridge ctytenon v0.3.0, which needs go-cty v1.19.0
// (see tenon.go). go mod tidy can't see tenon.go and would remove tenon, so
// change the versions with go get instead:
//
//	go build -modfile=tenon.mod -o ../../bin/hcl-tenon-adapter main.go tenon.go
//	go get -modfile=tenon.mod github.com/kmoneil/tenon@<version> github.com/kmoneil/tenon/ctytenon@<version>
