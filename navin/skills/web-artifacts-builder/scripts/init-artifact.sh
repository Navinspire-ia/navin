#!/usr/bin/env bash
# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only
#
# init-artifact.sh <name> [--three] [--dry-run]
#
# Writes a Vite + React + TypeScript + Material UI + framer-motion project
# configured to bundle into a single HTML file, then runs `npm install`.
#   --three    also add three + @react-three/fiber + @react-three/drei and a sample scene
#   --dry-run  write the files and print the plan, but skip npm install
#   -h, --help show this help
set -euo pipefail

usage() { sed -n '5,11p' "$0" | sed 's/^# \{0,1\}//'; }

NAME=""
THREE=0
DRY=0
for arg in "$@"; do
  case "$arg" in
    -h|--help) usage; exit 0 ;;
    --three) THREE=1 ;;
    --dry-run) DRY=1 ;;
    -*) echo "error: unknown option $arg" >&2; usage >&2; exit 2 ;;
    *) if [ -n "$NAME" ]; then echo "error: only one project name is accepted" >&2; exit 2; fi; NAME="$arg" ;;
  esac
done
if [ -z "$NAME" ]; then echo "error: project name is required" >&2; usage >&2; exit 2; fi
if [ -e "$NAME" ]; then echo "error: $NAME already exists" >&2; exit 1; fi

mkdir -p "$NAME/src"

THREE_DEPS=""
if [ "$THREE" = 1 ]; then
  THREE_DEPS=',
    "three": "^0.165.0",
    "@react-three/fiber": "^8.16.0",
    "@react-three/drei": "^9.108.0"'
fi

cat > "$NAME/package.json" <<EOF
{
  "name": "$NAME",
  "private": true,
  "version": "0.1.0",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "tsc --noEmit && vite build",
    "preview": "vite preview"
  },
  "dependencies": {
    "react": "^18.3.1",
    "react-dom": "^18.3.1",
    "@mui/material": "^5.16.0",
    "@mui/icons-material": "^5.16.0",
    "@emotion/react": "^11.13.0",
    "@emotion/styled": "^11.13.0",
    "framer-motion": "^11.3.0"$THREE_DEPS
  },
  "devDependencies": {
    "@types/react": "^18.3.3",
    "@types/react-dom": "^18.3.0",
    "@vitejs/plugin-react": "^4.3.1",
    "typescript": "^5.5.4",
    "vite": "^5.4.0",
    "vite-plugin-singlefile": "^2.0.2"$( [ "$THREE" = 1 ] && printf ',\n    "@types/three": "^0.165.0"' )
  }
}
EOF

cat > "$NAME/vite.config.ts" <<'EOF'
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { viteSingleFile } from "vite-plugin-singlefile";

// One self-contained index.html: every script, style and asset is inlined.
export default defineConfig({
  plugins: [react(), viteSingleFile()],
  build: {
    target: "es2020",
    assetsInlineLimit: 100_000_000,
    cssCodeSplit: false,
    reportCompressedSize: false,
    rollupOptions: { output: { inlineDynamicImports: true } },
  },
});
EOF

cat > "$NAME/tsconfig.json" <<'EOF'
{
  "compilerOptions": {
    "target": "ES2020",
    "lib": ["ES2020", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "moduleResolution": "bundler",
    "jsx": "react-jsx",
    "strict": true,
    "skipLibCheck": true,
    "isolatedModules": true,
    "noEmit": true,
    "types": ["vite/client"]
  },
  "include": ["src"]
}
EOF

cat > "$NAME/index.html" <<EOF
<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>$NAME</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
EOF

cat > "$NAME/src/main.tsx" <<'EOF'
import React from "react";
import ReactDOM from "react-dom/client";
import { ThemeProvider, CssBaseline } from "@mui/material";
import { theme } from "./theme";
import App from "./App";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ThemeProvider theme={theme}>
      <CssBaseline />
      <App />
    </ThemeProvider>
  </React.StrictMode>,
);
EOF

cat > "$NAME/src/theme.ts" <<'EOF'
import { createTheme } from "@mui/material/styles";

// Navin palette
export const palette = {
  ink: "#17171a",
  paper: "#f4f3ee",
  lime: "#c6e88a",
  teal: "#0e7490",
  violet: "#7c3aed",
};

export const theme = createTheme({
  palette: {
    mode: "light",
    primary: { main: palette.lime, contrastText: palette.ink },
    secondary: { main: palette.teal },
    info: { main: palette.violet },
    background: { default: palette.paper, paper: "#ffffff" },
    text: { primary: palette.ink, secondary: "#4b4b52" },
  },
  shape: { borderRadius: 6 },
  spacing: 8,
  typography: {
    fontFamily: '"IBM Plex Sans", "Segoe UI", system-ui, sans-serif',
    h5: { fontWeight: 600, letterSpacing: "-0.01em" },
    button: { textTransform: "none", fontWeight: 600 },
  },
  components: {
    MuiAppBar: { styleOverrides: { root: { backgroundColor: palette.ink, color: palette.paper } } },
    MuiCard: { styleOverrides: { root: { border: "1px solid #e2e1da", boxShadow: "none" } } },
  },
});
EOF

SCENE_IMPORT=""
SCENE_BLOCK=""
if [ "$THREE" = 1 ]; then
  SCENE_IMPORT='import Scene from "./Scene";'
  SCENE_BLOCK='        <Box sx={{ height: 280, mb: 3, borderRadius: 1, overflow: "hidden", border: "1px solid #e2e1da" }}>
          <Scene />
        </Box>'
  cat > "$NAME/src/Scene.tsx" <<'EOF'
import { useRef } from "react";
import { Canvas, useFrame } from "@react-three/fiber";
import { OrbitControls } from "@react-three/drei";
import type { Mesh } from "three";

function Spinner() {
  const ref = useRef<Mesh>(null!);
  useFrame((_, delta) => {
    ref.current.rotation.x += delta * 0.4;
    ref.current.rotation.y += delta * 0.6;
  });
  return (
    <mesh ref={ref}>
      <torusKnotGeometry args={[1, 0.3, 128, 16]} />
      <meshStandardMaterial color="#c6e88a" metalness={0.2} roughness={0.4} />
    </mesh>
  );
}

export default function Scene() {
  return (
    <Canvas camera={{ position: [0, 0, 5] }} style={{ background: "#17171a" }}>
      <ambientLight intensity={0.6} />
      <directionalLight position={[3, 3, 3]} intensity={1.2} />
      <Spinner />
      <OrbitControls enablePan={false} />
    </Canvas>
  );
}
EOF
fi

cat > "$NAME/src/App.tsx" <<EOF
import { useMemo, useState } from "react";
import {
  AppBar, Toolbar, Typography, Container, TextField, Card, CardContent,
  CardActions, Button, Dialog, DialogTitle, DialogContent, DialogActions,
  Stack, Chip, Box,
} from "@mui/material";
import SearchIcon from "@mui/icons-material/Search";
import { motion } from "framer-motion";
$SCENE_IMPORT

type Item = { id: number; title: string; tag: string; summary: string };

// Sample data: replace with the user's real dataset.
const ITEMS: Item[] = [
  { id: 1, title: "Quarterly revenue", tag: "finance", summary: "Revenue by region for the last four quarters." },
  { id: 2, title: "Signup funnel", tag: "growth", summary: "Visit to activation conversion, weekly." },
  { id: 3, title: "Incident log", tag: "ops", summary: "Open incidents with owner and age." },
  { id: 4, title: "Hiring pipeline", tag: "people", summary: "Candidates per stage and time in stage." },
];

export default function App() {
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<Item | null>(null);

  const visible = useMemo(() => {
    const q = query.trim().toLowerCase();
    return q ? ITEMS.filter((i) => [i.title, i.tag, i.summary].some((f) => f.toLowerCase().includes(q))) : ITEMS;
  }, [query]);

  return (
    <>
      <AppBar position="sticky" elevation={0}>
        <Toolbar>
          <Typography variant="h6" sx={{ flexGrow: 1 }}>$NAME</Typography>
          <Chip label="Sample data" size="small" color="primary" />
        </Toolbar>
      </AppBar>
      <Container maxWidth="md" sx={{ py: 4 }}>
$SCENE_BLOCK
        <TextField
          fullWidth
          label="Filter"
          placeholder="Type to filter by title, tag or summary"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          InputProps={{ startAdornment: <SearchIcon sx={{ mr: 1, color: "text.secondary" }} /> }}
          sx={{ mb: 3 }}
        />
        {visible.length === 0 ? (
          <Typography color="text.secondary">No item matches "{query}".</Typography>
        ) : (
          <Stack spacing={2}>
            {visible.map((item, index) => (
              <motion.div key={item.id} initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.2, delay: index * 0.03 }}>
                <Card>
                  <CardContent>
                    <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 1 }}>
                      <Typography variant="h5">{item.title}</Typography>
                      <Chip label={item.tag} size="small" variant="outlined" color="secondary" />
                    </Stack>
                    <Typography color="text.secondary">{item.summary}</Typography>
                  </CardContent>
                  <CardActions>
                    <Button size="small" onClick={() => setSelected(item)}>Details</Button>
                  </CardActions>
                </Card>
              </motion.div>
            ))}
          </Stack>
        )}
      </Container>
      <Dialog open={selected !== null} onClose={() => setSelected(null)} fullWidth maxWidth="sm">
        <DialogTitle>{selected?.title}</DialogTitle>
        <DialogContent>
          <Typography>{selected?.summary}</Typography>
          <Box sx={{ mt: 2 }}><Chip label={selected?.tag} size="small" /></Box>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setSelected(null)}>Close</Button>
        </DialogActions>
      </Dialog>
    </>
  );
}
EOF

cat > "$NAME/.gitignore" <<'EOF'
node_modules
dist
EOF

echo "Created $NAME/"
find "$NAME" -type f | sort | sed 's/^/  /'
echo "Stack: vite, react 18, typescript, @mui/material, @mui/icons-material, @emotion/react, @emotion/styled, framer-motion, vite-plugin-singlefile$( [ "$THREE" = 1 ] && echo ', three, @react-three/fiber, @react-three/drei' )"

if [ "$DRY" = 1 ]; then
  echo "Dry run: skipping npm install. Next: cd $NAME && npm install && npm run dev -- --port 4300"
  exit 0
fi

if ! command -v npm >/dev/null 2>&1; then
  echo "error: npm not found; install Node 18+ then run: cd $NAME && npm install" >&2
  exit 1
fi
NODE_MAJOR=$(node -v | sed 's/^v//' | cut -d. -f1)
if [ "$NODE_MAJOR" -lt 18 ]; then
  echo "error: Node 18 or newer is required (found $(node -v))" >&2
  exit 1
fi
(cd "$NAME" && npm install)
echo "Ready. Next: cd $NAME && npm run dev -- --port 4300 (never 5173-5176), then bash bundle-artifact.sh $NAME"
