# Frontend : conventions

React 19 · TypeScript (strict) · Vite 8 · Tailwind v4 · Vitest + Testing Library · oxlint · pnpm. Toutes les commandes se lancent depuis `frontend/`.

## Structure
```
src/
├── App.tsx                       coquille : AuthProvider → LoginPage | NoOrganizationScreen | zone d'organisation
│                                 (sélecteur, onglets Chat · Membres · Journal · Organisation selon les permissions),
│                                 zone remontée avec key={organisation active}
├── lib/api.ts                    client typé du backend (miroir exact de backend/app/schemas) : request(), ApiError,
│                                 setCsrfToken(), setOnUnauthenticated(), une fonction par endpoint
├── lib/errors.ts                 messageFor(code) / errorMessage(err) : messages français par code d'erreur
├── lib/roles.ts                  libellés des rôles, canManageRole, manageableRoles (miroir d'authz.py, affichage seulement)
├── lib/format.ts                 dates et formats d'affichage
├── lib/sse.ts                    parseur SSE pour fetch POST (parseSSE pur + readSSE)
├── features/auth/                AuthProvider (contexte de session), useAuth, LoginPage
├── features/organizations/       useOrganizations, OrganizationSwitcher, NoOrganizationScreen, MyInvitations,
│                                 CreateOrganizationForm, OrganizationSettings
├── features/members/             useMembers, MembersPanel, InviteForm
├── features/audit/               useAuditLog, AuditLogPanel, auditFormat
├── features/chat/                useAgents, useChat, ChatPanel (liste des agents + conversation)
└── test/                         setup.ts (jest-dom, cleanup, requête non simulée = échec), fetch.ts (mockFetch,
                                  callsTo), fixtures.ts (session, organisations, membres…)
```
En dev, Vite redirige `/api/*` vers `http://localhost:8000` (`vite.config.ts`). Le code appelle donc toujours `/api/...`, sans URL absolue. Le frontend et l'API sont ainsi sur la **même origine**, ce qui est indispensable pour le cookie de session (`SameSite=Lax`) et pour l'URI de redirection Google (`http://localhost:5173/api/auth/callback`).

## Sécurité côté interface (spec 001)
- **Le serveur est la seule autorité.** L'interface masque les écrans et les choix non permis, mais elle ne protège rien : chaque action reste contrôlée par le backend.
- **Tester des permissions, jamais des rôles** : `organization.permissions.includes("members:manage")`, et non `role === "admin"`. Seul `lib/roles.ts` connaît la correspondance rôle → permission de gestion, pour filtrer les listes déroulantes.
- **Jeton CSRF** : obtenu par `GET /api/auth/session`, gardé **en mémoire du module** `lib/api.ts` (jamais `localStorage` ni `sessionStorage`), ajouté par `request()` en `X-CSRF-Token` sur toute méthode non sûre.
- **Organisation active** : passée **explicitement** (`orgId`) à chaque fonction d'organisation, envoyée en `X-Organization-Id`. Elle est propre à l'onglet. La seule clé `localStorage` autorisée est `forge.activeOrganizationId`, qui sert à choisir l'organisation d'un nouvel onglet et est toujours revalidée contre la liste des organisations.
- **401** : `setOnUnauthenticated` ramène à la page de connexion avec « Votre session a pris fin ».
- **Messages d'erreur** : n'afficher que `messageFor(code)` / `errorMessage(err)`, jamais `error.message` ni un texte brut du serveur (FR-47). Un événement SSE `error` s'affiche aussi avec un message générique.
- **Aucune image distante** (l'avatar Google n'est pas affiché).

## Règles
- `pnpm add` uniquement. Pas de routeur ni de bibliothèque de cache de requêtes : hooks et contexte suffisent (constitution VII).
- Chaque appel au backend passe par `lib/api.ts`, dont les types reflètent exactement `backend/app/schemas/`. Toute modification d'un schéma Pydantic impose la mise à jour de `api.ts` dans la même tâche. Les événements SSE sont typés par l'union `StreamEvent` et validés à la lecture.
- Les tests portent sur le comportement visible : `getByRole`, `getByLabelText`, `findByText`. Simule `fetch` avec `mockFetch` (`src/test/fetch.ts`, routes `"MÉTHODE /chemin"`) et vérifie les appels avec `callsTo`. Une requête sans route simulée fait **échouer** le test. Pour un flux SSE simulé : `new Response('event: token\ndata: {"text":"x"}\n\n')`.
- Textes visibles en français. Accessibilité clavier : onglets selon le motif WAI-ARIA (flèches, Début, Fin), formulaires avec labels.
- Pas de `any`, pas de `!` non justifié. L'état d'une fonctionnalité vit dans un hook (`useX`), et le composant se contente de l'afficher. Les composants de gestion sont pilotés par props (`onChanged`).
- En dev, `StrictMode` double les requêtes au montage : c'est normal.
- Vérifications : `pnpm lint`, `pnpm typecheck` et `pnpm test`, tous verts.
