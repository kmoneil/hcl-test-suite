module hcl-test-suite/adapters/go

go 1.26.4

require (
	github.com/hashicorp/hcl/v2 v2.24.0
	github.com/zclconf/go-cty v1.18.0
)

require (
	github.com/agext/levenshtein v1.2.1 // indirect
	github.com/apparentlymart/go-textseg/v15 v15.0.0 // indirect
	github.com/mitchellh/go-wordwrap v1.0.1 // indirect
	golang.org/x/mod v0.17.0 // indirect
	golang.org/x/sync v0.14.0 // indirect
	golang.org/x/text v0.25.0 // indirect
	golang.org/x/tools v0.21.1-0.20240508182429-e35e4ccd0d2d // indirect
)

// Builds the adapter with the HCL that OpenTofu v1.12.6 ships: its fork of hashicorp/hcl
// and its go-cty version, as in the replace directive and requirements of OpenTofu's go.mod.
//
//	go build -modfile=opentofu.mod -o ../../bin/hcl-opentofu-adapter .
replace github.com/hashicorp/hcl/v2 => github.com/opentofu/hcl/v2 v2.20.2-0.20251021132045-587d123c2828
