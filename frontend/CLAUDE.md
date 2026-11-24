# Frontend : conventions

React 19 · TypeScript (strict) · Vite 8 · Tailwind v4 · Vitest + Testing Library · oxlint · pnpm. Toutes les commandes se lancent depuis `frontend/`.

## Structure
```
src/
├── App.tsx                  sélection de l'agent + layout
├── lib/api.ts               client typé du backend (miroir de backend/app/schemas)
├── lib/sse.ts               parseur SSE pour fetch POST (parseSSE pur + readSSE)
├── features/<feature>/      Composant.tsx, useHook.ts, *.test.tsx
└── test/setup.ts            jest-dom + cleanup
```
En dev, Vite redirige `/api/*` vers `http://localhost:8000` (`vite.config.ts`). Le code appelle donc toujours `/api/...`, sans URL absolue.

## Règles
- `pnpm add` uniquement.
- Chaque appel au backend passe par `lib/api.ts`, et les événements SSE sont typés par l'union `StreamEvent`. Un nouvel événement côté backend doit être ajouté à cette union.
- Les tests portent sur le comportement visible : `getByRole`, `getByLabelText`, `findByText`. Simule `fetch` avec `vi.spyOn(globalThis, 'fetch')`. Pour un flux SSE simulé : `new Response('event: token\ndata: {"text":"x"}\n\n')`.
- Pas de `any`, pas de `!` non justifié. L'état d'une fonctionnalité vit dans un hook (`useX`), et le composant se contente de l'afficher.
- Vérifications : `pnpm lint`, `pnpm typecheck` et `pnpm test`, tous verts.
