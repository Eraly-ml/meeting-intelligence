# Development tools

The product runs speech recognition, reasoning, and storage locally. The tools below help developers build it; they are not dependencies of the shipped application. Context7 and the hosted Sosumi service retrieve documentation over the internet. Send them API names and synthetic examples, never meeting recordings, transcripts, participant details, or the hub token.

At initial setup, this coding session exposed none of the requested MCP tools. The workspace also had no Playwright skill or CLI integration, and the active Apple developer directory was Command Line Tools rather than a full Xcode installation. The examples below are prepared configuration, not a claim that those tools have been connected or tested. No global MCP settings or global packages were changed.

| Tool | Use in this project | Connection |
| --- | --- | --- |
| XcodeBuildMCP | Discover Swift packages and Xcode schemes; build, run, and test the native client | Local stdio process |
| Context7 | Resolve the exact library before looking up FastAPI, Pydantic, and HTTPX behavior | Local stdio process calling a remote documentation service |
| Sosumi | Fetch Apple API availability, capture lifecycle guidance, and WWDC transcripts | Hosted HTTP MCP |
| Playwright CLI and skill | Exercise the Scriberr browser uploads, reports, and exports | Installed locally in `.local/browser-tools`; used against the Radxa deployment |

## Reviewable Codex configuration example

This is an example for a project `.codex/config.toml`, or for merging into existing personal configuration. Preserve existing settings. `cwd`, `env_vars`, and timeout values in seconds follow the official [Codex MCP configuration documentation](https://learn.chatgpt.com/docs/extend/mcp?surface=cli).

```toml
[mcp_servers.XcodeBuildMCP]
command = "npx"
args = ["-y", "xcodebuildmcp@2.7.0", "mcp"]
cwd = "/Users/nurifgx/орммр"
startup_timeout_sec = 60
tool_timeout_sec = 300

[mcp_servers.XcodeBuildMCP.env]
XCODEBUILDMCP_SENTRY_DISABLED = "true"
XCODEBUILDMCP_ENABLED_WORKFLOWS = "macos,swift-package,project-discovery,doctor"

[mcp_servers.context7]
command = "npx"
args = ["-y", "@upstash/context7-mcp"]
env_vars = ["CONTEXT7_API_KEY"]
startup_timeout_sec = 60
tool_timeout_sec = 60

[mcp_servers.sosumi]
url = "https://sosumi.ai/mcp"
startup_timeout_sec = 30
tool_timeout_sec = 60
```

Set `cwd` to your actual checkout. Supply `CONTEXT7_API_KEY` in the environment of the process launching Codex; the configuration contains the variable name, not the secret. Some GUI launches do not inherit terminal environment variables. Use the client’s supported secret or environment setup if needed. Context7 documents API-key authentication for stdio and the `CONTEXT7_API_KEY` environment variable; obtain your own key through its dashboard. An absent key may be limited by anonymous access. [Context7 authentication and transports](https://context7.com/docs/resources/all-clients), [environment-variable setup](https://context7.com/docs/resources/developer).

The XcodeBuildMCP version above is pinned to the release shown by its documentation when this example was prepared. Context7 uses the upstream package command; pin the version actually installed once the team has verified it. The first `npx` startup may download packages. Cache or install the verified development tools before an offline working session.

XcodeBuildMCP 2.x requires the final `mcp` argument. It otherwise starts in CLI mode. macOS and Swift package workflows must be selected explicitly because its default is the simulator workflow. The telemetry environment variable above disables its Sentry SDK. [Version 2 migration](https://www.xcodebuildmcp.com/docs/migration-v2), [workflow catalog](https://www.xcodebuildmcp.com/docs/workflows), [telemetry controls](https://www.xcodebuildmcp.com/docs/privacy).

For MCP clients that cannot connect to Sosumi over HTTP, its documented stdio proxy is:

```json
{
  "command": "npx",
  "args": ["-y", "mcp-remote", "https://sosumi.ai/mcp"]
}
```

Use either connection, not both. Sosumi’s public setup does not specify an API key. Hosting its renderer locally is possible, but the renderer still fetches Apple documentation; that alone does not make documentation lookup offline. [Sosumi’s project documentation](https://github.com/nshipster/sosumi.ai#readme).

## Build and documentation workflow

XcodeBuildMCP requires macOS 14.5+, full Xcode 16+, and Node.js 18+ for its npm path. This application targets macOS 15. The initial environment’s Swift compiler can validate the Swift package, but full Xcode remains necessary to exercise the requested Xcode workflow. Install Xcode and select its developer directory before checking the environment with the documented doctor command. [XcodeBuildMCP installation](https://www.xcodebuildmcp.com/docs/installation).

```sh
npx --package xcodebuildmcp@2.7.0 xcodebuildmcp-doctor
codex mcp list
```

Start a fresh coding session after connecting the servers, inspect the available tools, and run the XcodeBuildMCP Swift package workflow against `macos/Package.swift`. Use project discovery to obtain actual schemes if an Xcode project is introduced. Do not invent a scheme name. Native microphone and screen-recording permission prompts still need a human-operated macOS run; the XcodeBuildMCP UI automation workflow is scoped to iOS simulators.

Use Context7 to resolve a library ID first, then query that library’s documentation. For Apple code, use Sosumi’s documentation search and fetch tools to check `SCStreamConfiguration.captureMicrophone`, `SCStreamOutputType.microphone`, audio sample formats, and stop-capture completion. Cite the underlying Apple page when documenting API behavior. Inspect the local SDK as well: compiling against the actual deployment target catches availability and signature mismatches that a web example can miss.

## Browser tooling when needed

The Scriberr browser dashboard is tested with Microsoft's Playwright CLI and its skill. This checkout installs it privately with `npm install --prefix .local/browser-tools @playwright/cli`, then runs `playwright-cli install --skills` inside that directory. The skill is `.local/browser-tools/.claude/skills/playwright-cli/SKILL.md`. Browser profiles, screenshots, logs, and pairing credentials remain under ignored `.local/` paths. [Official Playwright CLI and skill instructions](https://github.com/microsoft/playwright-cli#readme).

Browser tests should target the local dashboard and use fixture recordings. Native SwiftUI flows continue to use native build/test tools and manual capture checks.
