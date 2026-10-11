# ftb-print evals

The evals run against `files/kennedy.ged`, a sample tree of the Kennedy family from
[D-Jeffrey/gedcom-samples](https://github.com/D-Jeffrey/gedcom-samples/tree/main/sample-kennedy)
(`MIT OR CC0-1.0`), so no private genealogy data is needed. Point the `ftb` MCP server at it, e.g. in
the `.mcp.json` of the directory the evals run from:

```json
{"mcpServers": {"ftb": {"type": "stdio", "command": "uv",
  "args": ["run", "--directory", "<ftb-mcp checkout>", "ftb-mcp", "--transport", "stdio",
           "--gedcom-path", "<skill>/evals/files/kennedy.ged"]}}}
```

Prompts that need a follow-up answer give the simulated answer in `expected_output`.
