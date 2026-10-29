# TypeScript server walkthrough (official SDK)

The official TypeScript SDK registers tools with zod schemas and serves them
over stdio or streamable HTTP. This walkthrough uses the `@modelcontextprotocol/sdk`
package line (1.x). A newer package line split into `@modelcontextprotocol/server`
and sibling packages exists; before starting a new project, check the SDK
README (`https://raw.githubusercontent.com/modelcontextprotocol/typescript-sdk/main/README.md`)
and follow whichever line the project already depends on. Do not mix the two
in one code base.

## Project layout

```
notes-mcp-server/
  package.json
  tsconfig.json
  README.md
  src/
    index.ts        # entry: build server, pick transport
    server.ts       # createServer(): registers tools, resources, prompts
    store.ts        # the capability being exposed
    errors.ts       # upstream error to message mapping
  dist/             # build output, dist/index.js is the executable
```

### package.json

```json
{
  "name": "notes-mcp-server",
  "version": "0.1.0",
  "description": "MCP server exposing a notes workspace",
  "type": "module",
  "bin": { "notes-mcp-server": "dist/index.js" },
  "files": ["dist"],
  "scripts": {
    "build": "tsc",
    "start": "node dist/index.js",
    "dev": "tsx src/index.ts",
    "inspect": "npx @modelcontextprotocol/inspector node dist/index.js"
  },
  "engines": { "node": ">=20" },
  "dependencies": {
    "@modelcontextprotocol/sdk": "^1.12.0",
    "express": "^4.21.0",
    "zod": "^3.23.0"
  },
  "devDependencies": {
    "@types/express": "^4.17.21",
    "@types/node": "^22.0.0",
    "tsx": "^4.19.0",
    "typescript": "^5.6.0"
  }
}
```

`express` and its types are only needed for the HTTP transport; a stdio-only
server can drop them.

### tsconfig.json

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "module": "Node16",
    "moduleResolution": "Node16",
    "outDir": "./dist",
    "rootDir": "./src",
    "strict": true,
    "esModuleInterop": true,
    "skipLibCheck": true,
    "declaration": false,
    "sourceMap": true
  },
  "include": ["src/**/*"]
}
```

## Complete small server

`src/store.ts` holds the capability (an in-memory notes store here, an API
client in a real server):

```typescript
export interface Note {
  id: string;
  title: string;
  folder: string;
  body: string;
  tags: string[];
}

export class NoteStore {
  private notes = new Map<string, Note>();

  constructor(seed: Note[] = []) {
    for (const note of seed) this.notes.set(note.id, note);
  }

  search(query: string, folder?: string): Note[] {
    const q = query.toLowerCase();
    return [...this.notes.values()].filter(
      (n) =>
        (folder === undefined || n.folder === folder) &&
        (n.title.toLowerCase().includes(q) || n.body.toLowerCase().includes(q))
    );
  }

  folders(): string[] {
    return [...new Set([...this.notes.values()].map((n) => n.folder))].sort();
  }

  get(id: string): Note | undefined {
    return this.notes.get(id);
  }
}
```

`src/server.ts` registers everything on a fresh server instance:

```typescript
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";
import { NoteStore } from "./store.js";

const CHARACTER_LIMIT = 25_000;

export function createServer(store: NoteStore): McpServer {
  const server = new McpServer({ name: "notes-mcp-server", version: "0.1.0" });

  server.registerTool(
    "notes_search",
    {
      title: "Search notes",
      description:
        "Search notes by text in title or body, optionally inside one folder. " +
        "Returns a page of matching notes (id, title, folder, tags) with pagination fields. " +
        "To read the full body of one note call notes_get with its id. " +
        'Example: {"query": "budget", "folder": "finance", "limit": 5}',
      inputSchema: {
        query: z.string().min(1).max(200).describe("Text to find in titles and bodies"),
        folder: z.string().optional().describe("Restrict to one folder path such as 'finance'"),
        limit: z.number().int().min(1).max(100).default(20).describe("Maximum notes to return"),
        offset: z.number().int().min(0).default(0).describe("Notes to skip, for pagination"),
        response_format: z
          .enum(["markdown", "json"])
          .default("markdown")
          .describe("'markdown' for readable text, 'json' for structured data")
      },
      annotations: { readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: false }
    },
    async ({ query, folder, limit, offset, response_format }) => {
      if (folder !== undefined && !store.folders().includes(folder)) {
        return {
          isError: true,
          content: [{ type: "text", text: `Folder '${folder}' not found. Call notes_list_folders to see valid folder paths.` }]
        };
      }
      const matches = store.search(query, folder);
      let page = matches.slice(offset, offset + limit);
      let text = render(page, matches, offset, query, folder, response_format);
      while (page.length > 1 && text.length > CHARACTER_LIMIT) {
        page = page.slice(0, Math.ceil(page.length / 2));
        text = render(page, matches, offset, query, folder, response_format);
      }
      return { content: [{ type: "text", text }] };
    }
  );

  server.registerTool(
    "notes_list_folders",
    {
      title: "List folders",
      description: "List every folder path in the workspace. Use a result as the folder argument of notes_search.",
      inputSchema: {},
      annotations: { readOnlyHint: true, idempotentHint: true }
    },
    async () => {
      const folders = store.folders();
      return { content: [{ type: "text", text: folders.length ? folders.join("\n") : "The workspace has no folders yet." }] };
    }
  );

  server.registerResource(
    "note",
    new ResourceTemplate("notes://{id}", { list: undefined }),
    { title: "Note body", description: "Full markdown body of one note" },
    async (uri, { id }) => {
      const note = store.get(String(id));
      if (!note) throw new Error(`note ${id} not found`);
      return { contents: [{ uri: uri.href, mimeType: "text/markdown", text: `# ${note.title}\n\n${note.body}` }] };
    }
  );

  server.registerPrompt(
    "summarise_folder",
    { title: "Summarise a folder", argsSchema: { folder: z.string() } },
    ({ folder }) => ({
      messages: [{ role: "user", content: { type: "text", text: `Search notes in folder '${folder}', read each one, then write a five-line summary.` } }]
    })
  );

  return server;
}

import { ResourceTemplate } from "@modelcontextprotocol/sdk/server/mcp.js";
import type { Note } from "./store.js";

function render(
  page: Note[], matches: Note[], offset: number, query: string, folder: string | undefined, format: "markdown" | "json"
): string {
  const nextOffset = offset + page.length;
  const hasMore = nextOffset < matches.length;
  if (format === "json") {
    return JSON.stringify(
      { total: matches.length, count: page.length, offset, items: page, has_more: hasMore, next_offset: hasMore ? nextOffset : null },
      null, 2
    );
  }
  if (page.length === 0) return `No notes match '${query}'${folder ? ` in folder ${folder}` : ""}.`;
  const lines = [`## ${matches.length} note(s) match '${query}'`, ""];
  for (const n of page) lines.push(`- ${n.title} (${n.id}) in ${n.folder}${n.tags.length ? ` [${n.tags.join(", ")}]` : ""}`);
  if (hasMore) lines.push("", `More results: call again with offset=${nextOffset}.`);
  return lines.join("\n");
}
```

(Move the two trailing imports to the top of the file in real code; they are
shown late here to keep the tool registration readable.)

`src/index.ts` picks the transport:

```typescript
#!/usr/bin/env node
import { readFileSync } from "node:fs";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { StreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/streamableHttp.js";
import express from "express";
import { createServer } from "./server.js";
import { NoteStore } from "./store.js";

const seedFile = process.env.NOTES_SEED_FILE;
const store = new NoteStore(seedFile ? JSON.parse(readFileSync(seedFile, "utf8")) : []);

async function main(): Promise<void> {
  const transport = process.env.MCP_TRANSPORT ?? "stdio";
  if (transport === "stdio") {
    const server = createServer(store);
    await server.connect(new StdioServerTransport());
    console.error("notes-mcp-server ready on stdio"); // stderr only: stdout is the protocol
    return;
  }

  const app = express();
  app.use(express.json());
  app.post("/mcp", async (req, res) => {
    // Stateless mode: a fresh server and transport per request, no session id.
    const server = createServer(store);
    const http = new StreamableHTTPServerTransport({ sessionIdGenerator: undefined });
    res.on("close", () => { void http.close(); void server.close(); });
    await server.connect(http);
    await http.handleRequest(req, res, req.body);
  });
  app.get("/mcp", (_req, res) => { res.status(405).end(); });
  const port = Number(process.env.NOTES_MCP_PORT ?? 8000);
  app.listen(port, "127.0.0.1", () => console.error(`notes-mcp-server listening on http://127.0.0.1:${port}/mcp`));
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
```

Points to notice:

- `inputSchema` is a zod shape; `.describe()` text becomes the field
  description in the JSON schema. Arguments are validated before the handler
  runs, and the handler's parameter types are inferred.
- Expected failures return `isError: true` with a next step. A thrown error
  also becomes a tool error with its message as text.
- Over HTTP, build a new server per request in stateless mode. It scales
  without session bookkeeping and avoids cross-request state bugs.
- Only `console.error` is used. `console.log` would corrupt the stdio stream.

## Bearer token on the HTTP endpoint

```typescript
const TOKEN = process.env.NOTES_MCP_TOKEN;
if (!TOKEN) { console.error("NOTES_MCP_TOKEN is required"); process.exit(1); }
app.use("/mcp", (req, res, next) => {
  const header = req.header("authorization") ?? "";
  if (header !== `Bearer ${TOKEN}`) { res.status(401).json({ error: "unauthorized" }); return; }
  next();
});
```

Add an `Origin` check for browsers and keep the bind address `127.0.0.1`
unless a TLS-terminating proxy sits in front.

## Build, run, test

```bash
npm install
npm run build                      # tsc -> dist/
node dist/index.js                 # stdio, waits silently
MCP_TRANSPORT=http node dist/index.js

python3 navin/skills/mcp-builder/scripts/connections.py list-tools \
  --transport stdio --command node --arg dist/index.js
python3 navin/skills/mcp-builder/scripts/connections.py call \
  --transport http --url http://127.0.0.1:8000/mcp \
  --tool notes_search --args '{"query":"budget","limit":2}'
npm run inspect                    # interactive MCP Inspector in the browser
```

Publishing with `bin` and the shebang line lets Navin start the server with
`npx -y notes-mcp-server@0.1.0` from a plugin `mcp.json`; pin the version.

## Quality checklist

- Package and server named `<service>-mcp-server`; tool names
  `<service>_<verb>_<object>`.
- Every tool has title, description for the model, zod schema with
  `.describe()` on each field, annotations.
- Listing tools paginate and cap size by returning fewer items.
- Errors are tool errors with a next step; nothing internal leaks.
- `npm run build` is clean under `strict`; nothing writes to stdout.
- `connections.py list-tools` shows the expected schemas; the evaluation set
  passes.
