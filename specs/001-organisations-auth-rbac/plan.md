# Plan technique : spec 001 — Organisations, connexion Google, rôles d'organisation et isolation des données

> Le comment. Écrit après validation de `spec.md`. Décisions structurantes : [ADR-0002](../../docs/adr/0002-architecture-rag-multi-organisations.md) (accepté) et [ADR-0003](../../docs/adr/0003-persistance-et-contexte-de-securite.md) (**proposé**, à valider avec ce plan).

## Résumé de l'approche

Postgres 18 dans Docker (`compose.yaml`), accès via `psycopg` 3 + SQLAlchemy 2 Core async, migrations Alembic qui versionnent aussi le RLS, les rôles et les droits. L'API se connecte avec un rôle `forge_app` sans `BYPASSRLS` et propriétaire de rien ; chaque requête ouvre **une** transaction qui pose `app.user_id` et `app.organization_id` (`set_config(..., true)`, équivalent de `SET LOCAL`). Connexion Google en BFF écrite dans Forge (`httpx` + `PyJWT`, code + PKCE + state + nonce), session serveur en table `sessions` (hash SHA-256 du cookie), jeton CSRF synchroniseur. Les permissions sont définies dans `app/core/authz.py` et vérifiées par `require("perm")`, une dépendance FastAPI ; un test refuse toute route sans permission ni exemption. Le frontend ajoute un contexte d'authentification, l'en-tête `X-Organization-Id` et `X-CSRF-Token` dans `lib/api.ts`, sans routeur. Les conversations restent dans `InMemorySaver`, mais une table `conversations` sous RLS (organisation + propriétaire) contrôle l'accès.

---

## Décisions techniques (pourquoi, et ce qu'on écarte)

Chaque décision est détaillée dans les sections suivantes. Ce tableau sert de carte.

| # | Sujet | Choix | Pourquoi | Écarté (en bref) |
|---|---|---|---|---|
| T1 | Pilote Postgres | `psycopg` 3 (`psycopg[binary]`) | Un seul pilote pour toute la feuille de route : le checkpointer Postgres de LangGraph et `procrastinate` (ADR-0002 D6) l'exigent. API sync (migrations, tests SQL) **et** async (API). | `asyncpg` : plus rapide et sans souci sous Windows, mais async seulement, et on aurait deux pilotes dès la 003. |
| T2 | Couche SQL | SQLAlchemy 2 **Core** async (`AsyncConnection`, `select()`, `text()`) | Le cœur de cette spec, c'est du SQL (RLS, verrous, fonctions) : Core le laisse visible, tout en évitant les chaînes SQL concaténées (requêtes toujours paramétrées). | ORM (`AsyncSession`) : carte d'identité, chargements implicites interdits en async, unité de travail cachée. SQL brut : pas d'Alembic. |
| T3 | Migrations | Alembic, `env.py` **synchrone** (psycopg sync), exécuté par `forge_owner` | Standard, historique et retour arrière. Le RLS, les `GRANT` et les fonctions sont du SQL explicite (`op.execute`) dans les révisions : ils sont revus et versionnés comme le reste. | Autogénération du RLS : Alembic ne gère pas les politiques. Migrations au démarrage de l'API : il faudrait donner des droits DDL au rôle applicatif. |
| T4 | Rôles Postgres | `forge_owner` (migrations), `forge_app` (API, `NOBYPASSRLS`, propriétaire de rien), `forge_self_reader` (`NOLOGIN`, possède 3 fonctions de lecture) ; `ENABLE` + `FORCE ROW LEVEL SECURITY` partout | Le rôle applicatif ne peut pas contourner le RLS, même s'il le voulait. `FORCE` protège aussi contre les requêtes lancées par le propriétaire. | Un seul rôle propriétaire : le propriétaire ignore le RLS sans `FORCE`. Rôle `BYPASSRLS` : c'est le « super-admin » que FR-36 interdit. |
| T5 | Contexte RLS | Une transaction par requête ; `SELECT set_config('app.organization_id', :org, true)` ; politiques via `app_current_org()` = `NULLIF(current_setting('app.organization_id', true), '')::uuid` | `SET LOCAL` n'accepte pas de paramètre lié : `set_config(..., true)` est son équivalent paramétrable. Le `NULLIF` traite le piège du pool (voir § Données). | `SET` de session : le contexte fuirait vers la requête suivante qui réutilise la connexion. |
| T6 | Lectures « à moi » transverses | 3 fonctions `SECURITY DEFINER` en lecture seule, possédées par `forge_self_reader`, qui a ses **propres** politiques RLS limitées à l'utilisateur courant | « Mes organisations » et « mes invitations » traversent les organisations. Le RLS reste actif, même dans ces fonctions. | Politique « mes adhésions » ouverte à `forge_app` : viole AC-42. Définisseur `BYPASSRLS` : contournement total. |
| T7 | Organisation active | En-tête `X-Organization-Id`, revérifié à chaque requête | Chaque onglet sait dans quelle organisation il agit : pas de mélange entre onglets (US-6). Le serveur n'a pas d'état caché. | Stockée en session : partagée entre onglets. Dans le chemin : change toutes les routes de la spec 000. |
| T8 | OIDC | Code maison : `httpx` + `PyJWT[crypto]`, derrière `IdentityProvider` | Chaque étape (state, nonce, PKCE, échange, validation de l'ID token) est lisible et testée. Environ 150 lignes. | Authlib 1.9 : `authlib.jose` déprécié (vérifié dans le paquet), intégration Starlette qui impose `SessionMiddleware` et un second cookie signé. `google-auth` : synchrone. |
| T9 | État de connexion (avant session) | Table `login_requests` (hash d'un cookie `forge_login`, state, nonce, code_verifier, usage unique) | Lie la réponse de Google à **ce** navigateur (FR-5) et rend le rejeu impossible côté serveur, sans clé de signature à gérer. | Cookie signé : rejouable tant qu'il n'a pas expiré, et il faut un secret de plus. |
| T10 | Session | Table `sessions`, clé = SHA-256 du jeton aléatoire (32 octets) ; cookie `forge_session` `HttpOnly; Secure; SameSite=Lax; Path=/` | Une fuite de la base ne donne aucun cookie utilisable. Un jeton de 256 bits n'a pas besoin d'un hachage lent. | JWT : non révocable. bcrypt/argon2 : inutile pour un secret aléatoire, et lent à chaque requête. |
| T11 | CSRF | Jeton synchroniseur : aléatoire par session, stocké dans `sessions.csrf_token`, livré par `GET /api/auth/session`, renvoyé dans `X-CSRF-Token` sur toute méthode non sûre | Le jeton est lié à la session et vérifié côté serveur (`hmac.compare_digest`). Combiné à `SameSite=Lax`, c'est la défense recommandée par l'OWASP. | Double soumission par cookie : il faut la signer pour la lier à la session, donc un secret de plus. Contrôle d'`Origin` seul : insuffisant. |
| T12 | Checkpointer | On garde `InMemorySaver` ; table `conversations` (RLS organisation + propriétaire) ; clé LangGraph composée `org/user/conversation` | FR-32 est garanti sans persister les checkpoints. Un checkpointer Postgres en 001 ajouterait des tables sans `organization_id`, créées par `setup()` avec des droits DDL. | `AsyncPostgresSaver` dès maintenant : reporté à la 003, qui tranchera avec `psycopg` déjà en place. |
| T13 | Base de test | Même conteneur Docker, base dédiée `forge_test`, migrée au début de la session pytest, `TRUNCATE` entre les tests | Le même moteur, la même version et les mêmes politiques que la cible (FR-50), sans nouvelle dépendance. | testcontainers : dépendance en plus, 5 à 10 s par session. SQLite : ne prouverait rien sur le RLS. |
| T14 | Horloge | `Clock` injectable (`app.state.clock`) ; les services reçoivent `now` | Les expirations (AC-8, 9, 24) se testent sans attendre et sans dépendance. Le SQL n'utilise jamais `now()` pour décider. | `time-machine` / `freezegun` : ne figent pas le `now()` de Postgres, et c'est une dépendance en plus. |
| T15 | Journal plateforme | Journalisation Python structurée (`logging`, logger `forge.security`), avec une liste fermée de champs | C'est le canal prévu (console en local, Cloud Logging en cible, D14). Il se teste avec `caplog`. | Table en base : aucun écran ne la lit dans la 001. |
| T16 | Frontend | Pas de routeur, pas de bibliothèque de requêtes : un contexte `AuthProvider` et des hooks `useX` | Cinq écrans, aucun lien profond exigé (constitution VII). | `react-router` 8, TanStack Query : utiles plus tard (008), prématurés ici. |

---

## Conformité constitution

| Principe | Respecté ? | Note |
|---|---|---|
| I. Spec d'abord | ✅ | Spec 001 validée, sans marqueur. Les écarts trouvés sont listés dans « Remarques sur la spec », sans modifier la spec. |
| II. TDD | ✅ | Chaque AC a un test nommé (table de traçabilité). `tasks.md` imposera test rouge → code → vert. |
| III. Pas de vrai LLM en test | ✅ | `fake_model` conservé. Google est simulé par `httpx.MockTransport` (`tests/fakes/google.py`). Une fixture autouse bloque toute connexion réseau qui ne vise pas la boucle locale (AC-58). |
| IV. Agents déterministes | ✅ | Aucun agent modifié. Les services (adhésions, invitations) sont des fonctions typées testables sans HTTP. |
| V. Un seul chemin vers le modèle | ✅ | `get_chat_model()` inchangé. |
| VI. Contrat API | ✅ | Tous les endpoints, schémas Pydantic et types TS sont listés ci-dessous. Format d'erreur unique `{code, message}`. |
| VII. Simplicité | ✅ | 4 dépendances Python d'exécution justifiées (+ `httpx` qui passe de dev à exécution), 0 dépendance frontend. Pas de routeur, pas d'ORM, pas de checkpointer persistant, pas de testcontainers. |
| VIII. Observabilité | ✅ | Journal d'audit par organisation (table) + journal de sécurité plateforme (`forge.security`). L'événement SSE `error` est inchangé. |
| IX. Sécurité | ✅ | Secrets uniquement dans `.env` (identifiants Google, mots de passe des rôles locaux). Entrées validées par Pydantic. Cookies durcis, CSRF, PKCE, aucun secret dans les journaux (filtre sur le journal d'accès d'uvicorn). |
| X. Isolation des organisations | ✅ | 5 tables d'organisation, toutes avec `organization_id` (ou `id` pour `organizations`), `ENABLE` + `FORCE` RLS, politique `TO forge_app`, test de fuite paramétré par table. Le code filtre **aussi** explicitement. Un test d'introspection échoue si une table n'est ni globale (liste blanche) ni protégée. |
| XI. Aucune élévation de privilèges | ✅ | Le code ne teste que des permissions (`require`, `can_manage_role`). Le passage rôle → permission vit dans `authz.py` seul. Aucune auto-élévation possible (règle « il faut la permission de gestion du rôle source **et** du rôle cible »). Les Admins et les Propriétaires ne lisent pas les conversations des autres : c'est garanti par le RLS (`user_id = app_current_user()`). |
| XII. Documents = données | N/A | Aucun document ni contenu externe. L'avatar Google n'est pas une réponse d'agent. |

---

## Conception de l'agent (si applicable)

N/A : aucun agent n'est créé ni modifié. Changements limités à la route `app/api/routes/agents.py` :

- `GET /api/agents`, `POST /api/agents/{name}/invoke` et `/stream` exigent `require("agents:use")` (session + organisation active + permission).
- `thread_id` devient un `UUID` créé **par le serveur**. Un `thread_id` fourni doit correspondre à une ligne `conversations` visible (même organisation, même propriétaire, même agent), sinon 404 `conversation_not_found`.
- La clé passée au checkpointer est `f"{organization_id}/{user_id}/{conversation_id}"` : défense en profondeur, une erreur de contrôle ne ferait pas tomber sur le checkpoint d'un autre.
- Le travail en base (contrôle et création de la conversation) se fait **avant** de renvoyer l'`EventSourceResponse`. La transaction est validée par une dépendance `Depends(..., scope="function")` (vérifié dans FastAPI 0.165) : aucune connexion Postgres n'est retenue pendant le flux du LLM.
- `AgentRegistry`, `studio.py` et `langgraph.json` sont inchangés.

---

## Architecture et flux

### Connexion Google (BFF, code + PKCE)

```mermaid
sequenceDiagram
  participant B as Navigateur
  participant V as Vite :5173 (proxy /api)
  participant A as API FastAPI
  participant D as Postgres
  participant G as Google
  B->>V: GET /api/auth/login (navigation)
  V->>A: idem
  A->>D: INSERT login_requests(hash(T), state, nonce, verifier, expires_at)
  A-->>B: 302 vers Google (scope "openid email profile", state, nonce, code_challenge S256)<br/>Set-Cookie forge_login=T (HttpOnly, Lax, 10 min)
  B->>G: consentement
  G-->>B: 302 vers /api/auth/callback?code&state
  B->>A: GET /api/auth/callback (cookie forge_login=T)
  A->>D: UPDATE login_requests SET consumed_at WHERE hash=hash(T) AND consumed_at IS NULL AND non expirée RETURNING state, nonce, verifier
  Note over A: refus si pas de ligne, ou state différent (compare_digest)
  A->>G: POST token endpoint (code, code_verifier, client_secret)
  G-->>A: id_token (+ access_token, jeté)
  Note over A: vérifie signature JWKS, iss, aud, azp, exp, nonce, email_verified
  A->>D: upsert users par google_sub ; révoque l'ancienne session présentée ; INSERT sessions(hash(S), csrf)
  A-->>B: 303 vers APP_BASE_URL/ ; Set-Cookie forge_session=S (HttpOnly, Secure, Lax) ; efface forge_login
```

### Requête authentifiée dans une organisation

```mermaid
sequenceDiagram
  participant B as Navigateur
  participant A as API (dépendances)
  participant D as Postgres (rôle forge_app)
  B->>A: POST /api/organization/invitations<br/>Cookie forge_session, X-CSRF-Token, X-Organization-Id
  A->>D: BEGIN
  A->>D: SELECT session + user WHERE token_hash = sha256(cookie)
  Note over A: 401 si absente, révoquée, inactive > 24 h, créée > 7 j (horloge injectée)
  A->>D: UPDATE sessions SET last_seen_at
  Note over A: méthode non sûre : 403 csrf_failed si l'en-tête ≠ sessions.csrf_token
  A->>D: set_config('app.user_id', uid, true), set_config('app.organization_id', org, true)
  A->>D: SELECT role FROM memberships WHERE organization_id=org AND user_id=uid (filtre explicite + RLS)
  Note over A: 404 organization_not_found si aucune ligne (même réponse qu'une organisation inexistante)
  Note over A: require("members:manage") : 403 + audit permission.denied (transaction séparée)
  A->>D: logique métier (requêtes filtrées par org, RLS en dessous) + INSERT audit_events
  A->>D: COMMIT
  A-->>B: 201
```

---

## Autorisation (`app/core/authz.py`)

### Permissions

| Niveau | Permission | Usage |
|---|---|---|
| Utilisateur connecté (toute session valide) | `session:read` | `GET /api/auth/session` |
| | `session:logout` | `POST /api/auth/logout` |
| | `organization:list` | `GET /api/organizations` |
| | `organization:create` | `POST /api/organizations` |
| | `invitation:respond` | lister, accepter ou refuser **ses** invitations |
| Organisation active | `organization:read` | voir l'organisation active, son rôle, ses permissions |
| | `organization:leave` | quitter |
| | `organization:rename` | renommer |
| | `agents:use` | agents intégrés (spec 000) |
| | `members:read` | lister les membres et les invitations en attente |
| | `members:manage` | gérer les Membres et les Invités (inviter, retirer, changer de rôle, révoquer) |
| | `admins:manage` | nommer ou retirer des Admins |
| | `owners:manage` | nommer ou retirer des Propriétaires |
| | `audit:read` | consulter le journal d'audit |

### Rôles → permissions (seul endroit où les rôles sont nommés)

| Permission | owner | admin | member | guest |
|---|:-:|:-:|:-:|:-:|
| `organization:read`, `organization:leave` | ✅ | ✅ | ✅ | ✅ |
| `agents:use` | ✅ | ✅ | ✅ | ❌ |
| `members:read`, `members:manage`, `audit:read` | ✅ | ✅ | ❌ | ❌ |
| `admins:manage`, `owners:manage`, `organization:rename` | ✅ | ❌ | ❌ | ❌ |

```python
ROLE_PERMISSIONS: dict[Role, frozenset[Permission]]          # table ci-dessus
USER_PERMISSIONS: frozenset[Permission]                      # niveau « utilisateur connecté »
ROLE_MANAGEMENT_PERMISSION: dict[Role, Permission] = {       # « pour gérer un porteur du rôle X, il faut… »
    Role.GUEST: "members:manage", Role.MEMBER: "members:manage",
    Role.ADMIN: "admins:manage", Role.OWNER: "owners:manage",
}
def permissions_for(role: Role) -> frozenset[Permission]: ...   # lu à chaque appel (AC-28)
def can_manage_role(perms, role) -> bool: ...                   # inviter / retirer / révoquer
def can_change_role(perms, from_role, to_role) -> bool:         # FR-27, FR-30
    return can_manage_role(perms, from_role) and can_manage_role(perms, to_role)
```

Pourquoi cette règle : FR-18, FR-27 et FR-30 se réduisent à « il faut avoir le droit de gérer le rôle actuel de la cible **et** le rôle visé ». L'auto-élévation (AC-37) tombe toute seule : un Admin qui veut devenir Propriétaire n'a pas `owners:manage`, et un Membre n'a même pas `members:manage`. Conséquence assumée : un Admin ne peut pas se rétrograder lui-même (il n'a pas `admins:manage`), mais il peut quitter l'organisation (voir « Remarques sur la spec »).

La règle « au moins un Propriétaire » (FR-28) mentionne `Role.OWNER` : c'est un **invariant métier**, pas un contrôle d'accès. Elle vit dans `services/memberships.py` et verrouille les lignes des Propriétaires (`SELECT … WHERE role = 'owner' FOR UPDATE`) pour que deux rétrogradations simultanées ne vident pas l'organisation.

### Dépendances FastAPI

- `require(perm)` → `Annotated[OrgContext, Depends(...)]` : session valide → CSRF si méthode non sûre → contexte d'organisation → `perm in permissions_for(role)` → sinon audit `permission.denied` + 403.
- `require_authenticated(perm)` → `Principal` : session valide → CSRF si méthode non sûre → `perm in USER_PERMISSIONS`.
- La fonction de dépendance porte un attribut `__forge_permission__`. Le test AC-29 parcourt `app.routes` et l'arbre `route.dependant.dependencies` à la recherche de cet attribut.
- `PUBLIC_ROUTES = frozenset({"health", "auth_login", "auth_callback"})` (noms d'opérations) : c'est la seule liste d'exemptions, et le test vérifie qu'elle vaut exactement cela.
- `/docs`, `/redoc` et `/openapi.json` ne sont servis que si `ENVIRONMENT=dev` : en test et en production, ils n'existent pas. Sinon ils seraient des fonctions exposées hors liste d'exemptions.

### Refus journalisés sans être annulés

Un refus lève une exception, qui annule (`ROLLBACK`) la transaction de la requête, et avec elle une écriture d'audit faite dans cette transaction. Le helper `RequestDb.record_denial(action, target, details)` fait donc : `ROLLBACK` → nouvelle transaction → nouveau `set_config` du contexte → `INSERT audit_events(outcome='denied')` → `COMMIT` → lève l'`ApiError`. Il sert aux refus de permission (`require`), aux refus de règle (rôle non attribuable, dernier Propriétaire) et à l'invitation expirée à l'usage.

Les tentatives sur une organisation dont on n'est **pas** membre ne vont pas dans le journal de cette organisation (on n'y est pas). Elles vont au journal plateforme (`organization_access_denied`). Voir « Remarques sur la spec ».

---

## Contrat API

Toutes les routes sont sous `/api`. Format d'erreur unique (`app/schemas/errors.py`) : `ErrorBody {code: str, message: str}`. Le `message` est en anglais et générique. Le frontend traduit `code` en français (FR-47). Les 422 de validation gardent le format FastAPI.

Codes : `401 not_authenticated` · `403 csrf_failed` · `403 permission_denied` · `400 organization_required` (en-tête absent ou mal formé) · `404 organization_not_found` · `404 not_found` (membre, invitation) · `404 conversation_not_found` · `404 agent_not_found` · `409 already_member` · `409 invitation_already_pending` · `409 last_owner` · `410 invitation_expired`.

**403 ou 404 (FR-37)** : une ressource d'une autre organisation est invisible sous RLS, donc le code suit le même chemin que pour un identifiant inexistant, et la réponse est identique octet pour octet (404). On ne répond 403 que si la personne agit dans **son** organisation et n'a pas la permission : elle sait déjà que la ressource existe.

| Méthode | Chemin | Permission | Requête | Réponse | Erreurs |
|---|---|---|---|---|---|
| GET | `/health` | exemptée | — | `{"status":"ok"}` | — |
| GET | `/auth/login` | exemptée | — | 302 vers Google + cookie `forge_login` | 302 vers `/?login_error=unavailable` |
| GET | `/auth/callback` | exemptée | query `code`, `state` (ou `error`) | 303 vers `/` + cookie `forge_session` | 303 vers `/?login_error=failed\|cancelled` |
| GET | `/auth/session` | `session:read` | — | `SessionOut` | 401 |
| POST | `/auth/logout` | `session:logout` | — | 204, efface le cookie | 401, 403 csrf |
| GET | `/organizations` | `organization:list` | — | `list[OrganizationSummary]` | 401 |
| POST | `/organizations` | `organization:create` | `OrganizationCreate` | 201 `OrganizationSummary` | 401, 403 csrf, 422 |
| GET | `/invitations/mine` | `invitation:respond` | — | `list[MyInvitation]` | 401 |
| POST | `/invitations/{invitation_id}/accept` | `invitation:respond` | — | `OrganizationSummary` | 401, 403 csrf, 404, 409 already_member, 410 |
| POST | `/invitations/{invitation_id}/decline` | `invitation:respond` | — | 204 | 401, 403 csrf, 404, 410 |
| GET | `/organization` | `organization:read` | en-tête org | `ActiveOrganization` | 400, 401, 404 |
| PATCH | `/organization` | `organization:rename` | `OrganizationRename` | `ActiveOrganization` | 400, 401, 403, 404, 422 |
| POST | `/organization/leave` | `organization:leave` | — | 204 | 400, 401, 403 csrf, 404, 409 last_owner |
| GET | `/organization/members` | `members:read` | — | `list[Member]` | 400, 401, 403, 404 |
| PATCH | `/organization/members/{user_id}` | `members:manage` + `can_change_role` | `MemberRoleUpdate` | `Member` | 403, 404, 409 last_owner |
| DELETE | `/organization/members/{user_id}` | `members:manage` + `can_manage_role` | — | 204 | 403, 404, 409 last_owner |
| GET | `/organization/invitations` | `members:read` | — | `list[Invitation]` (en attente, non expirées) | 403, 404 |
| POST | `/organization/invitations` | `members:manage` + `can_manage_role(role)` | `InvitationCreate` | 201 `Invitation` | 403, 409 already_member, 409 invitation_already_pending, 422 |
| DELETE | `/organization/invitations/{invitation_id}` | `members:manage` + `can_manage_role(inv.role)` | — | 204 | 403, 404 |
| GET | `/organization/audit` | `audit:read` | query `before_id?`, `limit` (1 à 200, 50 par défaut) | `AuditPage` | 403, 404 |
| GET | `/agents` | `agents:use` | en-tête org | `list[AgentInfo]` | 400, 401, 403, 404 |
| POST | `/agents/{name}/invoke` | `agents:use` | `RunRequest` | `RunResponse` | 403, 404 agent / conversation |
| POST | `/agents/{name}/stream` | `agents:use` | `RunRequest` | SSE inchangé (`metadata`, `token`, `tool`, `error`, `done`) | idem, **avant** l'ouverture du flux |

Toutes les routes « organisation » et « agents » exigent `X-Organization-Id: <uuid>`. Toutes les méthodes non sûres (`POST`, `PATCH`, `PUT`, `DELETE`) exigent `X-CSRF-Token`.

Aucun endpoint ne modifie ni ne supprime une entrée d'audit (AC-50).

### Schémas Pydantic (`backend/app/schemas/`)

```python
# common
Role = Literal["owner", "admin", "member", "guest"]
Permission = Literal["organization:read", "organization:leave", "organization:rename", "agents:use",
                     "members:read", "members:manage", "admins:manage", "owners:manage", "audit:read"]
# auth.py
class UserOut(BaseModel): id: UUID; email: str; name: str; picture_url: str | None
class SessionOut(BaseModel): user: UserOut; csrf_token: str
# organizations.py
class OrganizationCreate(BaseModel): name: str = Field(min_length=1, max_length=100)  # strip appliqué
class OrganizationRename(OrganizationCreate): ...
class OrganizationSummary(BaseModel): id: UUID; name: str; role: Role
class ActiveOrganization(OrganizationSummary): permissions: list[Permission]
# members.py
class Member(BaseModel): user_id: UUID; name: str; email: str; picture_url: str | None; role: Role; joined_at: datetime
class MemberRoleUpdate(BaseModel): role: Role
# invitations.py
class InvitationCreate(BaseModel): email: str = Field(max_length=320, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$"); role: Role
class UserRef(BaseModel): user_id: UUID; name: str
class Invitation(BaseModel): id: UUID; email: str; role: Role; invited_by: UserRef; created_at: datetime; expires_at: datetime
class MyInvitation(BaseModel): id: UUID; organization_id: UUID; organization_name: str; role: Role; invited_by_name: str; expires_at: datetime
# audit.py
AuditAction = Literal["organization.created", "organization.renamed", "invitation.created", "invitation.accepted",
                      "invitation.declined", "invitation.revoked", "invitation.expired_on_use",
                      "member.role_changed", "member.removed", "member.left", "permission.denied"]
class AuditActor(BaseModel): user_id: UUID | None; name: str | None
class AuditEvent(BaseModel): id: int; occurred_at: datetime; actor: AuditActor; action: AuditAction
                             target_type: Literal["organization", "member", "invitation"] | None
                             target_id: str | None; outcome: Literal["success", "denied"]
                             details: dict[str, str | int | bool | None]   # ex. {"from_role":"member","to_role":"guest"}
class AuditPage(BaseModel): items: list[AuditEvent]; next_before_id: int | None
# agents.py (modifié)
class RunRequest(BaseModel): message: str = Field(min_length=1); thread_id: UUID | None = None
class RunResponse(BaseModel): thread_id: UUID; output: str          # sérialisé en chaîne, inchangé côté JSON
# errors.py
class ErrorBody(BaseModel): code: str; message: str
```

L'email n'est pas validé par `EmailStr`, qui ajouterait la dépendance `email-validator`. Une forme minimale suffit : seul un utilisateur Google à l'adresse **vérifiée** identique peut accepter.

### Types TypeScript (`frontend/src/lib/api.ts`)

```ts
export type Role = 'owner' | 'admin' | 'member' | 'guest'
export type Permission = 'organization:read' | 'organization:leave' | 'organization:rename' | 'agents:use'
  | 'members:read' | 'members:manage' | 'admins:manage' | 'owners:manage' | 'audit:read'
export interface User { id: string; email: string; name: string; picture_url: string | null }
export interface SessionInfo { user: User; csrf_token: string }
export interface OrganizationSummary { id: string; name: string; role: Role }
export interface ActiveOrganization extends OrganizationSummary { permissions: Permission[] }
export interface Member { user_id: string; name: string; email: string; picture_url: string | null; role: Role; joined_at: string }
export interface UserRef { user_id: string; name: string }
export interface Invitation { id: string; email: string; role: Role; invited_by: UserRef; created_at: string; expires_at: string }
export interface MyInvitation { id: string; organization_id: string; organization_name: string; role: Role; invited_by_name: string; expires_at: string }
export type AuditAction = 'organization.created' | 'organization.renamed' | 'invitation.created' | 'invitation.accepted'
  | 'invitation.declined' | 'invitation.revoked' | 'invitation.expired_on_use' | 'member.role_changed'
  | 'member.removed' | 'member.left' | 'permission.denied'
export interface AuditEvent { id: number; occurred_at: string; actor: { user_id: string | null; name: string | null }
  action: AuditAction; target_type: 'organization' | 'member' | 'invitation' | null; target_id: string | null
  outcome: 'success' | 'denied'; details: Record<string, string | number | boolean | null> }
export interface AuditPage { items: AuditEvent[]; next_before_id: number | null }
export class ApiError extends Error { constructor(public status: number, public code: string, message: string) }
// StreamEvent : inchangé
```

Fonctions : `getSession`, `logout`, `listMyOrganizations`, `createOrganization`, `listMyInvitations`, `acceptInvitation`, `declineInvitation`, `getActiveOrganization(orgId)`, `renameOrganization(orgId, name)`, `leaveOrganization(orgId)`, `listMembers(orgId)`, `changeMemberRole(orgId, userId, role)`, `removeMember(orgId, userId)`, `listInvitations(orgId)`, `createInvitation(orgId, email, role)`, `revokeInvitation(orgId, id)`, `listAuditEvents(orgId, beforeId?)`, `listAgents(orgId)`, `streamAgent(orgId, agent, message, threadId?, signal?)`, et la constante `LOGIN_URL = '/api/auth/login'`.

Un helper interne `request()` ajoute `X-CSRF-Token` sur les méthodes non sûres (jeton gardé **en mémoire** dans le module, posé par `setCsrfToken()`), ajoute `X-Organization-Id` quand un `orgId` est fourni, et appelle le gestionnaire `onUnauthenticated` enregistré quand il reçoit un 401. L'`orgId` est passé **explicitement** à chaque fonction d'organisation : l'organisation visée se voit à l'appel, et aucun état global ne peut être périmé.

---

## Données et persistance

### Modèle

```mermaid
erDiagram
  users ||--o{ sessions : "a"
  users ||--o{ memberships : "a"
  organizations ||--o{ memberships : "contient"
  organizations ||--o{ invitations : "émet"
  organizations ||--o{ conversations : "contient"
  organizations ||--o{ audit_events : "journalise"
  users ||--o{ conversations : "possède"
```

| Table | Portée | Colonnes clés | RLS (politique `TO forge_app`) | Droits `forge_app` |
|---|---|---|---|---|
| `users` | globale (FR-33) | `id uuid PK`, `google_sub text UNIQUE`, `email`, `email_normalized` (index, **non** unique), `name`, `picture_url`, `created_at`, `last_login_at` | aucune (globale) | SELECT, INSERT, UPDATE |
| `sessions` | globale | `token_hash bytea PK`, `user_id FK`, `csrf_token text`, `created_at`, `last_seen_at`, `revoked_at` | aucune | SELECT, INSERT, UPDATE |
| `login_requests` | globale, avant authentification | `token_hash bytea PK`, `state`, `nonce`, `code_verifier`, `created_at`, `expires_at`, `consumed_at` | aucune | SELECT, INSERT, UPDATE, DELETE |
| `organizations` | organisation | `id uuid PK`, `name` (CHECK 1 à 100), `created_at` | `id = app_current_org()` (USING + WITH CHECK) | SELECT, INSERT, UPDATE (pas de DELETE : suppression hors périmètre) |
| `memberships` | organisation | PK `(organization_id, user_id)`, `role` (CHECK dans les 4 rôles), `created_at`, `updated_at` | `organization_id = app_current_org()` | SELECT, INSERT, UPDATE, DELETE |
| `invitations` | organisation | `id uuid PK`, `organization_id`, `email_normalized`, `role`, `invited_by FK`, `created_at`, `expires_at`, `status` (CHECK `pending\|accepted\|declined\|revoked\|expired`), `decided_at`, `decided_by` ; index unique partiel `(organization_id, email_normalized) WHERE status='pending'` | `organization_id = app_current_org()` | SELECT, INSERT, UPDATE |
| `conversations` | organisation + propriétaire | `id uuid PK` (= thread_id), `organization_id`, `user_id`, `agent_name`, `created_at` | `organization_id = app_current_org() AND user_id = app_current_user()` | SELECT, INSERT |
| `audit_events` | organisation | `id bigint identity PK`, `organization_id`, `occurred_at`, `actor_user_id`, `action`, `target_type`, `target_id`, `outcome`, `details jsonb` | `organization_id = app_current_org()` | **SELECT, INSERT seulement** (append-only) + USAGE sur la séquence |

Les noms d'organisation ne sont **pas** uniques : une contrainte d'unicité globale révélerait qu'un nom est déjà pris, donc qu'une organisation existe (FR-16).

La table `conversations` est celle que l'ADR-0002 D1 appelle `threads` : on garde le mot de la spec.

### Rôles, migrations et versionnement du RLS

- **Création des rôles et des bases** : `infra/postgres/initdb/10-roles-and-databases.sql`, exécuté une seule fois par l'image Docker à l'initialisation du volume, en superutilisateur. Les mots de passe sont lus dans l'environnement avec `\getenv` (psql ≥ 15), donc aucun secret dans le fichier. Le script crée `forge_owner` (LOGIN, NOSUPERUSER, NOBYPASSRLS), `forge_app` (LOGIN, NOSUPERUSER, NOBYPASSRLS, NOCREATEDB, NOCREATEROLE), `forge_self_reader` (NOLOGIN, NOBYPASSRLS), les bases `forge` et `forge_test` (propriétaire `forge_owner`), et `GRANT forge_self_reader TO forge_owner WITH INHERIT FALSE, SET TRUE` (pour que les migrations puissent lui donner les fonctions, sans hériter de ses droits).
  Pourquoi hors Alembic : créer des rôles demande des privilèges que le rôle de migration ne doit pas avoir, et cette étape diffère selon l'environnement (Docker, Cloud SQL).
- **Alembic** (`backend/migrations/`, `backend/alembic.ini`), connecté en `forge_owner` via `DATABASE_OWNER_URL` (`env.py` synchrone, psycopg) :
  - `0001_identity_and_organizations` : les 8 tables ci-dessus (`op.create_table`, à partir des `Table` de `app/db/tables.py`) ;
  - `0002_row_level_security` : fonctions `app_current_org()`, `app_current_user()` et `app_current_user_email()` ; `ENABLE` + `FORCE ROW LEVEL SECURITY` sur les 5 tables d'organisation ; politiques `TO forge_app` ; politiques `TO forge_self_reader` ; les 3 fonctions `SECURITY DEFINER` ; tous les `GRANT` / `REVOKE`. Chaque `upgrade` a son `downgrade`.
- **Règle pour les specs suivantes** (ajoutée à `backend/CLAUDE.md`) : toute table d'organisation porte `organization_id`, active et force le RLS, a une politique `TO forge_app` et des droits minimaux, dans la **même** migration. Le test `test_every_table_is_global_or_org_protected` échoue sinon.

### Contexte posé par requête

```sql
-- 0002 : NULLIF parce qu'après un SET LOCAL, la variable vaut '' (et non NULL)
-- sur la connexion réutilisée par le pool ; ''::uuid lèverait une erreur au lieu de ne rien renvoyer.
CREATE FUNCTION app_current_org() RETURNS uuid LANGUAGE sql STABLE
  AS $$ SELECT NULLIF(current_setting('app.organization_id', true), '')::uuid $$;
```

`app/db/context.py` : `RequestDb` enveloppe l'`AsyncConnection` de la requête. La dépendance `request_db` (`scope="function"`) fait `engine.connect()` → `begin()` → `yield` → `commit()`, ou `rollback()` en cas d'exception. Puis `set_user(uid)` et `set_organization(org)` exécutent `SELECT set_config(...)`.

**Variable absente (AC-45)** : `app_current_org()` renvoie `NULL`, `organization_id = NULL` est `NULL`, donc **aucune ligne** n'est visible, et toute insertion échoue au `WITH CHECK`. Le test couvre la connexion neuve **et** la connexion réutilisée après une transaction (cas `''`).

**Ce que le RLS protège, et ce qu'il ne protège pas** : il rattrape les filtres oubliés et les erreurs de logique. Il ne protège pas contre un attaquant qui ferait exécuter du SQL arbitraire au rôle `forge_app` : celui-ci pourrait appeler `set_config` lui-même. La parade à ce risque-là : uniquement des requêtes paramétrées, aucun SQL construit par concaténation (vérifié en revue).

### Lectures transverses « à moi » (`SECURITY DEFINER` restreintes)

| Fonction | Renvoie | Utilisée par |
|---|---|---|
| `app_my_memberships()` | `(organization_id, organization_name, role)` des adhésions de `app_current_user()` | `GET /organizations` |
| `app_my_pending_invitations(p_now timestamptz)` | invitations `pending`, non expirées, adressées à l'email de l'utilisateur courant, avec le nom de l'organisation et celui de l'invitant | `GET /invitations/mine` |
| `app_my_invitation(p_id uuid)` | `(organization_id, email_normalized, role, status, expires_at)` si l'invitation `p_id` est adressée à l'utilisateur courant | `accept`, `decline` |

Mécanisme précis :

1. Les fonctions sont `LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public` (le `search_path` figé empêche le détournement par un objet homonyme), possédées par `forge_self_reader`, avec `REVOKE EXECUTE … FROM PUBLIC` et `GRANT EXECUTE … TO forge_app`.
2. Dans la fonction, `current_user` vaut `forge_self_reader`. Ce rôle **n'a pas** `BYPASSRLS`, et les politiques d'organisation ne s'appliquent qu'à `forge_app`. Seules ses propres politiques, en lecture, s'appliquent :
   - `memberships` : `user_id = app_current_user()` ;
   - `invitations` : `email_normalized = app_current_user_email()` ;
   - `organizations` : il existe une adhésion **ou** une invitation `pending` de l'utilisateur courant vers cette organisation (sous-requêtes elles-mêmes soumises aux politiques ci-dessus ; aucune récursion) ;
   - `users` : pas de RLS (table globale).
3. Le corps de chaque fonction filtre **aussi** explicitement sur l'utilisateur courant. En Python, `accept` vérifie de nouveau que `email_normalized` correspond à celui de la session.
4. Même si une fonction est boguée, elle ne peut rien renvoyer d'autre que des données de l'utilisateur courant : c'est le RLS du rôle propriétaire qui l'interdit.
5. Un test d'introspection (`pg_proc.prosecdef`) vérifie que les seules fonctions `SECURITY DEFINER` sont ces trois-là, et qu'elles appartiennent à `forge_self_reader`.

Accepter une invitation : `app_my_invitation(id)` → absente ou non `pending` : 404 ; expirée (`now ≥ expires_at`) : passage en `expired` + audit `invitation.expired_on_use`, validés, puis 410 ; sinon `set_organization(inv.organization_id)`, puis `UPDATE invitations SET status='accepted' … WHERE id=:id AND status='pending' RETURNING` (usage unique, sûr en cas de concurrence), `INSERT memberships` (conflit : 409 `already_member`), audit, `COMMIT`. Le contexte n'est posé sur l'organisation de l'invitation **qu'après** que le serveur a prouvé que l'invitation s'adresse à l'utilisateur.

Créer une organisation : l'`id` est généré en Python (`uuid4`), puis `set_organization(id)` est posé **avant** l'`INSERT`. Le `WITH CHECK` passe, et l'organisation, l'adhésion Propriétaire et l'audit `organization.created` sont écrits dans la même transaction.

---

## Sessions, cookies et CSRF

- **Jeton de session** : `secrets.token_urlsafe(32)`. En base, `sha256(token)`. Le cookie : `forge_session`, `HttpOnly`, `Secure` (si `COOKIE_SECURE=true`), `SameSite=Lax`, `Path=/`, sans `Domain`, `Max-Age` = durée absolue.
- **Validité**, calculée avec `clock.now()` à chaque requête : `revoked_at IS NULL`, `now < last_seen_at + SESSION_IDLE_TIMEOUT_HOURS` (24 par défaut) et `now < created_at + SESSION_MAX_AGE_DAYS` (7 par défaut). Changer la configuration s'applique donc aussi aux sessions existantes. Chaque requête valide met `last_seen_at` à jour.
- **Fixation (FR-10)** : il n'existe aucune session avant la connexion. Le callback crée toujours une nouvelle session et révoque celle que le navigateur présentait éventuellement.
- **Déconnexion** : `revoked_at = now`, cookie effacé. Le cookie copié est refusé (401).
- **CSRF** : `csrf_token = secrets.token_urlsafe(32)` par session, renvoyé par `GET /auth/session`, vérifié avec `hmac.compare_digest` pour toute méthode non sûre, avant toute écriture. Le jeton est conservé en clair en base, parce qu'il faut pouvoir le renvoyer à chaque chargement de page ; seul, sans le cookie, il ne donne aucun pouvoir. Il n'apparaît jamais dans les journaux.
- **`COOKIE_SECURE`** : `true` par défaut. Le `.env.example` local le met à `false` (http://localhost). Un validateur de `Settings` refuse de démarrer si `ENVIRONMENT=prod` et `COOKIE_SECURE=false`. Les tests tournent en `https://testserver` avec `Secure` activé, comme en production.
- **Même origine** : en dev, le proxy Vite sert `/api` sur `localhost:5173`, ce qui rend le cookie et le callback same-origin. L'URI de redirection déclarée chez Google est `http://localhost:5173/api/auth/callback` (`OIDC_REDIRECT_URI`). Elle est lue dans la configuration, jamais déduite de l'en-tête `Host`. En cible, le frontend et l'API devront partager un domaine (spec 009).

### Fournisseur d'identité (`app/auth/`)

- `provider.py` : `class IdentityProvider(Protocol)` avec `authorization_url(state, nonce, code_challenge) -> str` et `async exchange(code, code_verifier, nonce) -> VerifiedIdentity(sub, email, email_verified, name, picture)`.
- `google.py` : `GoogleOIDCProvider(settings, http: httpx.AsyncClient)`. Il lit la découverte (`https://accounts.google.com/.well-known/openid-configuration`) et les JWKS, mis en cache (JWKS rechargés si le `kid` est inconnu). Scope exact : `openid email profile`, `prompt=select_account`, **sans** `access_type=offline` (aucun refresh token). PKCE S256 (vérificateur `token_urlsafe(64)`). Validation : `jwt.decode(id_token, key, algorithms=["RS256"], audience=client_id, issuer=["https://accounts.google.com", "accounts.google.com"], options={"require": ["exp", "iat", "sub", "aud", "iss"]})`, puis `azp` (si présent) = `client_id`, `nonce` = attendu (`compare_digest`), `email_verified is True`. Le claim `hd` est ignoré (FR-1). L'`access_token` est jeté sans être stocké.
- `create_app(model=None, *, settings=None, http_transport=None, clock=None)` : les tests injectent `httpx.MockTransport(fake_google.handler)`. En production, le transport est celui par défaut.
- Aucun « faux » fournisseur dans `app/` : le faux Google vit dans `tests/fakes/google.py` (FR-6).

---

## Journalisation

- **Audit d'organisation** (`services/audit.py`, table `audit_events`) : les actions de `AuditAction` ci-dessus. L'auteur est `actor_user_id` (l'invité lui-même pour `accepted`, `declined` et `expired_on_use`). `details` ne contient que des rôles, des noms de permission ou des noms d'organisation : jamais un secret (FR-41).
- **Journal plateforme** (`core/security_log.py`) : `security_event(name, *, user_id=None, organization_id=None, reason=None)`. La signature fermée empêche de passer un jeton par mégarde. Événements : `login_succeeded`, `login_failed` (raisons : `email_not_verified`, `state_mismatch`, `missing_login_cookie`, `replayed`, `invalid_id_token`, `provider_error`, `cancelled`), `logout`, `session_rejected` (`unknown`, `revoked`, `idle_expired`, `absolute_expired`), `organization_access_denied`. Pas d'email dans ces événements : seul l'identifiant Forge (minimisation).
- **Journal d'accès d'uvicorn** : il écrirait `/api/auth/callback?code=…&state=…`. Un filtre `RedactQueryFilter`, installé sur le logger `uvicorn.access` au démarrage de l'app, remplace la query string de `/api/auth/*` par `?[redacted]`.

---

## Frontend

Aucune nouvelle dépendance. L'état vit dans des hooks, les composants l'affichent.

| Élément | Rôle |
|---|---|
| `features/auth/AuthProvider.tsx` + `useAuth.ts` | Au montage : `getSession()`. 200 : `user`, `setCsrfToken()`. 401 : visiteur. Enregistre `onUnauthenticated` : si un utilisateur était connecté, il passe en visiteur avec le motif `session_ended` (FR-46, AC-55). Fournit `logout()`. |
| `features/auth/LoginPage.tsx` | Titre, lien-bouton « Se connecter avec Google » (`<a href="/api/auth/login">`, accessible au clavier), messages « Votre session a pris fin, reconnectez-vous » et « La connexion a échoué » (lit `?login_error`, puis nettoie l'URL avec `history.replaceState`). Rien d'autre (FR-42). |
| `features/organizations/useOrganizations.ts` | Liste `listMyOrganizations()`, organisation active (identifiant mémorisé dans `localStorage` sous `forge.activeOrganizationId` : ce n'est pas un secret, et il est revalidé contre la liste), `getActiveOrganization()` pour le rôle et les `permissions`, `switchTo(id)`, `refresh()`. |
| `OrganizationSwitcher.tsx` | `<select aria-label="Organisation">` natif (clavier). |
| `NoOrganizationScreen.tsx` | « Vous n'êtes membre d'aucune organisation », avec `MyInvitations` et `CreateOrganizationForm` (FR-17). |
| `MyInvitations.tsx` | Invitations en attente, boutons Accepter et Refuser. Aussi affiché dans l'application quand il y en a (FR-22). |
| `OrganizationSettings.tsx` | Renommer (si `organization:rename`), Quitter (erreur `last_owner` traduite). |
| `features/members/useMembers.ts`, `MembersPanel.tsx`, `InviteForm.tsx` | Liste, changement de rôle, retrait, invitations en attente, révocation. Les choix de rôle proposés sont filtrés par les permissions (`admins:manage`, `owners:manage`), le serveur reste l'autorité. |
| `features/audit/useAuditLog.ts`, `AuditLogPanel.tsx` | Tableau paginé (« Plus ancien », `before_id`). |
| `features/chat/` | `ChatPanel` et `useChat` reçoivent `orgId`. Sans `agents:use`, le message est « Aucun agent ne vous est partagé pour l'instant » (FR-45). Une erreur 403 sur les agents affiche le même message. |
| `App.tsx` | Chargement → `LoginPage` → `NoOrganizationScreen` → coquille : en-tête (sélecteur, nom de l'utilisateur, Déconnexion), onglets `Chat \| Membres \| Journal \| Organisation` (`useState`, pas de routeur), visibles selon les `permissions` (FR-44). `<main key={activeOrgId}>` remonte toute la zone au changement d'organisation : aucun état (fil de conversation, liste de membres) ne passe d'une organisation à l'autre. |
| `lib/errors.ts` | `code` → message français (FR-47), message générique par défaut, jamais de détail technique. |

États gérés partout : chargement, erreur traduite, vide (aucun membre en attente, journal vide). L'avatar Google est affiché avec `referrerPolicy="no-referrer"`.

---

## Environnement de développement et vérifications

- **`compose.yaml`** (racine) : service `db`, image `pgvector/pgvector:0.8.7-pg18-trixie`. C'est l'image prévue par l'ADR-0002 D5/D14 ; l'extension ne sera créée qu'en 003, et prendre l'image dès maintenant évite de changer d'image sur un volume existant. Port `127.0.0.1:${POSTGRES_PORT:-5433}:5432` : écoute locale seulement, et 5433 pour ne pas entrer en conflit avec un Postgres déjà installé. Volume nommé `forge_pgdata` monté sur **`/var/lib/postgresql`** (les images Postgres 18 ont déplacé `PGDATA`, à vérifier à l'implémentation), `./infra/postgres/initdb` en lecture seule, `healthcheck: pg_isready`.
- **`scripts/dev.ps1`** (FR-48, commande unique) : vérifie Docker et `.env` (message clair sinon) → `docker compose up -d --wait db` → `uv run alembic upgrade head` (dans `backend/`) → backend (`uv run uvicorn app.main:app --reload --port 8000 --loop app.core.aio:selector_loop_factory`) et frontend dans deux fenêtres, comme aujourd'hui.
- **`scripts/db-reset.ps1`** (FR-49) : demande une confirmation (`-Force` pour la sauter) → `docker compose down --volumes` → `up -d --wait db` → `alembic upgrade head`. Les données persistent sinon dans le volume nommé.
- **`scripts/check.ps1`** (FR-50, AC-58) : nouvelle étape `infra: database` (`docker compose up -d --wait db`) **avant** `backend: pytest`. Pytest migre `forge_test` au début de la session (`alembic upgrade head` en `forge_owner`) et vide les tables (`TRUNCATE … CASCADE`, en propriétaire) avant chaque test qui utilise la base. Si Postgres est injoignable, les tests de base **échouent** avec un message explicite (« lancez `docker compose up -d db` »). Ils ne sont jamais ignorés.
- **Réseau en test** : une fixture autouse remplace `socket.socket.connect` et refuse toute adresse autre que `127.0.0.1`, `::1` ou `localhost`. Google (MockTransport) et le LLM (`fake_model`) n'en ont pas besoin, et un appel réseau accidentel fait échouer le test.
- **Windows** : `app/core/aio.py::selector_loop_factory` pour uvicorn ; `TestClient(..., backend_options={"loop_factory": asyncio.SelectorEventLoop})` sous `win32` ; les tests SQL d'isolation utilisent l'API **synchrone** de psycopg (pas de boucle). Au démarrage, si la boucle courante est une `ProactorEventLoop`, l'app lève une erreur qui donne l'option `--loop` à utiliser.
- **`.gitattributes`** : `* text=auto`, et `*.sh` / `*.sql` en `eol=lf` (un script d'initialisation en CRLF casse dans le conteneur Linux).
- **Tests d'infrastructure** (AC-56, AC-57) : `tests/infra/`, marqueur `infra`, lancés seulement avec `RUN_INFRA=1` (comme `live`). Ils utilisent un projet compose séparé (`COMPOSE_PROJECT_NAME=forge-smoke`, port 5434) pour ne jamais toucher aux données du développeur. Ils ne font pas partie de `check.ps1` (arrêt et relance d'un conteneur, environ 20 s). `/spec-verify` les lancera.

### Variables `.env.example` ajoutées

```
# --- Database (local Docker) ---
POSTGRES_PORT=5433
POSTGRES_PASSWORD=change-me-superuser
FORGE_OWNER_PASSWORD=change-me-owner
FORGE_APP_PASSWORD=change-me-app
DATABASE_URL=postgresql+psycopg://forge_app:change-me-app@localhost:5433/forge
DATABASE_OWNER_URL=postgresql+psycopg://forge_owner:change-me-owner@localhost:5433/forge
TEST_DATABASE_URL=postgresql+psycopg://forge_app:change-me-app@localhost:5433/forge_test
TEST_DATABASE_OWNER_URL=postgresql+psycopg://forge_owner:change-me-owner@localhost:5433/forge_test
# --- Google sign-in (OAuth client "Web application" in Google Cloud console) ---
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
OIDC_REDIRECT_URI=http://localhost:5173/api/auth/callback
APP_BASE_URL=http://localhost:5173
# --- Sessions & invitations ---
COOKIE_SECURE=false
SESSION_IDLE_TIMEOUT_HOURS=24
SESSION_MAX_AGE_DAYS=7
INVITATION_TTL_DAYS=7
LOGIN_REQUEST_TTL_MINUTES=10
```

`DATABASE_OWNER_URL` n'est lue que par Alembic, jamais par l'API. `TEST_*` n'est lue que par `tests/conftest.py`.

---

## Fichiers impactés

| Fichier | Action | Raison |
|---|---|---|
| `compose.yaml` | créer | Postgres 18 + volume persistant |
| `infra/postgres/initdb/10-roles-and-databases.sql` | créer | rôles `forge_owner` / `forge_app` / `forge_self_reader`, bases `forge` et `forge_test` |
| `.gitattributes` | créer | fins de ligne LF pour les scripts exécutés dans le conteneur |
| `.env.example` | modifier | variables ci-dessus |
| `scripts/dev.ps1` | modifier | base + migrations + `--loop` |
| `scripts/db-reset.ps1` | créer | FR-49 |
| `scripts/check.ps1` | modifier | étape `infra: database` avant pytest |
| `backend/pyproject.toml`, `backend/uv.lock` | modifier | dépendances, marqueur `infra` |
| `backend/alembic.ini`, `backend/migrations/env.py`, `backend/migrations/script.py.mako` | créer | Alembic |
| `backend/migrations/versions/0001_identity_and_organizations.py` | créer | tables |
| `backend/migrations/versions/0002_row_level_security.py` | créer | RLS, FORCE, politiques, fonctions, GRANT |
| `backend/app/main.py` | modifier | `create_app(settings, http_transport, clock)`, moteur dans le lifespan, routers, gestionnaire `ApiError`, docs seulement en dev, filtre du journal d'accès |
| `backend/app/core/config.py` | modifier | nouveaux réglages + validateur production |
| `backend/app/core/aio.py` | créer | `selector_loop_factory` |
| `backend/app/core/clock.py` | créer | `Clock`, `SystemClock` |
| `backend/app/core/errors.py` | créer | `ApiError`, gestionnaire |
| `backend/app/core/security_log.py` | créer | journal plateforme + `RedactQueryFilter` |
| `backend/app/core/authz.py` | créer | permissions, rôles, `require`, `require_authenticated`, `PUBLIC_ROUTES` |
| `backend/app/db/__init__.py`, `engine.py`, `tables.py`, `context.py` | créer | moteur async, `Table` Core, `RequestDb` + dépendance `request_db` |
| `backend/app/auth/__init__.py`, `provider.py`, `google.py`, `sessions.py`, `dependencies.py` | créer | OIDC, sessions, session courante + CSRF |
| `backend/app/services/__init__.py`, `organizations.py`, `memberships.py`, `invitations.py`, `audit.py`, `conversations.py` | créer | logique métier (reçoit `RequestDb`, `now`) |
| `backend/app/api/routes/auth.py`, `organizations.py`, `members.py`, `invitations.py`, `audit.py` | créer | routes |
| `backend/app/api/routes/agents.py` | modifier | `require("agents:use")`, conversations, clé de thread composée |
| `backend/app/api/routes/health.py` | modifier | nom d'opération `health` (exemption) |
| `backend/app/schemas/errors.py`, `auth.py`, `organizations.py`, `members.py`, `invitations.py`, `audit.py` | créer | contrat |
| `backend/app/schemas/agents.py` | modifier | `thread_id: UUID` |
| `backend/tests/conftest.py` | modifier | base de test, horloge, faux Google, clients authentifiés, garde réseau |
| `backend/tests/fakes/__init__.py`, `fakes/google.py` | créer | faux Google (clé RSA, JWKS, codes à usage unique, contrôle PKCE, panne simulée) |
| `backend/tests/factories.py` | créer | monde de test Acme / Globex, utilisateurs, sessions |
| `backend/tests/test_api.py` | modifier | non-régression 000 avec un Membre authentifié |
| `backend/tests/auth/*.py`, `tests/api/*.py`, `tests/db/*.py`, `tests/services/*.py`, `tests/test_authz.py`, `tests/test_security_log.py`, `tests/test_no_network.py`, `tests/infra/test_dev_environment.py` | créer | voir la stratégie de test |
| `frontend/src/lib/api.ts` | modifier | types, `request()`, CSRF, en-tête org, `ApiError`, 401 |
| `frontend/src/lib/errors.ts`, `frontend/src/lib/api.test.ts` | créer | messages, tests du client |
| `frontend/src/App.tsx`, `frontend/src/App.test.tsx` | modifier / créer | coquille et parcours |
| `frontend/src/features/auth/*` | créer | `AuthProvider`, `useAuth`, `LoginPage` + tests |
| `frontend/src/features/organizations/*` | créer | sélecteur, aucun-org, invitations, création, réglages + tests |
| `frontend/src/features/members/*`, `frontend/src/features/audit/*` | créer | écrans de gestion + tests |
| `frontend/src/features/chat/useChat.ts`, `ChatPanel.tsx`, `ChatPanel.test.tsx` | modifier | `orgId`, message Invité |
| `docs/adr/0003-persistance-et-contexte-de-securite.md` | **créé (proposé)** | décisions T1 à T8 |
| `docs/architecture.md` | modifier | partie A : schéma, tables, API, flux d'authentification, mémoire des conversations ; partie B : D1, D2 et D3 « fait » ; tableau « Ce qui va changer » |
| `backend/CLAUDE.md` | modifier | structure, règles base (transaction par requête, jamais de `SET` de session, horloge, règle des tables d'organisation, `--loop` Windows, Alembic) |
| `frontend/CLAUDE.md` | modifier | `AuthProvider`, `orgId` explicite, CSRF, `ApiError`, permissions et jamais rôles |
| `CLAUDE.md`, `README.md` | modifier | commandes (`docker compose`, `db-reset.ps1`, `alembic`), prérequis (Docker Desktop, client OAuth Google) |

---

## Stratégie de test

Type : **unit** (sans HTTP ni base), **API** (`TestClient` + vraie base `forge_test`), **DB** (SQL direct en `forge_app`, psycopg sync), **UI** (Vitest + Testing Library), **infra** (`RUN_INFRA=1`). Aucun test `live` : rien ne dépend du LLM.

| Critère | Test (fichier::nom) | Type |
|---|---|---|
| AC-1 | `tests/auth/test_login.py::test_login_succeeds_for_workspace_and_gmail_accounts[workspace-gmail]` | API |
| AC-2 | `tests/auth/test_login.py::test_login_refused_when_email_not_verified` (aucune ligne `sessions`, aucun cookie, `login_failed` journalisé) | API |
| AC-3 | `tests/auth/test_login.py::test_relogin_with_changed_email_keeps_same_account` | API |
| AC-4 | `tests/auth/test_login.py::test_authorization_request_asks_only_basic_identity` (scope = `{openid, email, profile}`, ni Drive ni `access_type=offline`, PKCE S256, state et nonce présents) | API |
| AC-5 | `tests/auth/test_login.py::test_callback_rejected[missing_cookie-state_mismatch-replayed-nonce_mismatch-bad_audience-bad_issuer-bad_signature-expired_request]` | API |
| AC-6 | `tests/auth/test_login.py::test_login_response_has_no_google_token_and_hardened_cookie` | API |
| AC-7 | `tests/auth/test_login_paths.py::test_session_creation_only_from_google_callback` (analyse AST de `app/`) ; `::test_no_fake_identity_provider_in_app_package` | unit |
| AC-8 | `tests/auth/test_sessions.py::test_session_idle_timeout[23h59-ok-24h01-refused]` | API |
| AC-9 | `tests/auth/test_sessions.py::test_session_absolute_timeout_despite_activity` | API |
| AC-10 | `tests/auth/test_sessions.py::test_logout_revokes_server_side_session` | API |
| AC-11 | `tests/auth/test_login.py::test_pre_login_session_id_never_authenticated` | API |
| AC-12 | `tests/auth/test_csrf.py::test_every_mutating_route_requires_csrf[route]` (paramétré depuis `app.routes`) ; `::test_wrong_csrf_token_changes_nothing` ; `frontend/src/lib/api.test.ts` « adds X-CSRF-Token on unsafe methods » | API, UI |
| AC-13 | `tests/auth/test_sessions.py::test_authorized_request_works_when_identity_provider_down` (aucune requête vers le faux Google) | API |
| AC-14 | `tests/api/test_organizations.py::test_create_organization_makes_creator_sole_owner` | API |
| AC-15 | `tests/api/test_organizations.py::test_switch_active_organization_applies_new_role` | API |
| AC-16 | `tests/api/test_organizations.py::test_cannot_activate_organization_without_membership` (réponse identique à celle d'un uuid inexistant) | API |
| AC-17 | `tests/api/test_organizations.py::test_list_my_organizations_shows_only_memberships` | API |
| AC-18 | `tests/api/test_organizations.py::test_user_without_organization_cannot_use_agents` ; `frontend/src/features/organizations/NoOrganizationScreen.test.tsx` « shows pending invitations and create form » | API, UI |
| AC-19 | `tests/api/test_invitations.py::test_admin_invites_member` | API |
| AC-20 | `tests/api/test_invitations.py::test_admin_cannot_invite_admin_or_owner[admin-owner]` (audit `denied`) | API |
| AC-21 | `tests/api/test_invitations.py::test_invitee_accepts_with_case_insensitive_email` (puis réutilisation : 404) | API |
| AC-22 | `tests/api/test_invitations.py::test_other_email_cannot_accept_invitation` | API |
| AC-23 | `tests/api/test_invitations.py::test_invitee_declines_invitation` | API |
| AC-24 | `tests/api/test_invitations.py::test_expired_or_revoked_invitation_cannot_be_accepted[expired-revoked]` (`clock.advance(days=7)`) | API |
| AC-25 | `tests/api/test_invitations.py::test_admin_cannot_revoke_admin_invitation` | API |
| AC-26 | `tests/api/test_invitations.py::test_inviting_existing_member_is_refused` | API |
| AC-27 | `tests/api/test_permission_matrix.py::test_role_permission_matrix[role-action]` (matrice FR-23 recopiée telle quelle dans le test, indépendante de `authz.py`) | API |
| AC-28 | `tests/test_authz.py::test_changing_role_definition_changes_behavior` (`monkeypatch` de `ROLE_PERMISSIONS`, puis appel d'API) | API |
| AC-29 | `tests/api/test_route_protection.py::test_every_route_declares_permission_or_exemption` ; `::test_exemptions_are_health_and_login_only` | unit |
| AC-30 | `tests/api/test_route_protection.py::test_anonymous_request_to_protected_route_is_401[route]` (paramétré depuis `app.routes`) | API |
| AC-31 | `tests/api/test_members.py::test_demotion_to_guest_takes_effect_on_next_request` | API |
| AC-32 | `tests/api/test_members.py::test_removed_member_loses_access_keeps_other_memberships` | API |
| AC-33 | `tests/api/test_members.py::test_admin_cannot_modify_or_remove_admin_or_owner[...]` (audit `denied`) | API |
| AC-34 | `tests/api/test_members.py::test_owner_promotes_then_demotes_admin` | API |
| AC-35 | `tests/api/test_members.py::test_last_owner_cannot_be_removed_demoted_or_leave[remove-demote-leave]` ; `tests/services/test_memberships.py::test_concurrent_owner_demotions_keep_one_owner` (deux connexions) | API, DB |
| AC-36 | `tests/api/test_members.py::test_member_leaves_organization` | API |
| AC-37 | `tests/api/test_members.py::test_no_self_elevation[admin_to_owner-member_to_admin-member_to_owner]` | API |
| AC-38 | `tests/test_api.py::test_list_agents`, `::test_invoke_returns_answer_and_thread`, `::test_invoke_runs_custom_tool`, `::test_stream_emits_sse_events`, `::test_unknown_agent_returns_404` (adaptés : Membre authentifié) ; `frontend/src/features/chat/ChatPanel.test.tsx` (existant, avec l'en-tête org) | API, UI |
| AC-39 | `tests/api/test_agents_access.py::test_guest_cannot_list_or_invoke_agents[list-invoke-stream]` ; `frontend/src/features/chat/ChatPanel.test.tsx` « guest sees no shared agent message » | API, UI |
| AC-40 | `tests/api/test_conversations.py::test_other_user_cannot_continue_conversation[member-admin]` (réponse identique à celle d'un uuid inexistant ; le faux modèle n'a aucune réponse scriptée, donc l'appeler ferait échouer le test) | API |
| AC-41 | `tests/api/test_conversations.py::test_conversation_not_reachable_from_other_organization` | API |
| AC-42 | `tests/db/test_isolation.py::test_unfiltered_select_sees_only_active_org[organizations-memberships-invitations-conversations-audit_events]` | DB |
| AC-43 | `tests/db/test_isolation.py::test_unfiltered_update_delete_cannot_touch_other_org[table]` (et refus de privilège là où il n'y a ni `UPDATE` ni `DELETE`) | DB |
| AC-44 | `tests/db/test_isolation.py::test_insert_or_move_row_into_other_org_is_rejected[table]` | DB |
| AC-45 | `tests/db/test_isolation.py::test_no_context_returns_no_rows[table]` ; `::test_reused_connection_after_transaction_returns_no_rows` ; `tests/db/test_db_roles.py::test_app_role_cannot_bypass_rls` (non superutilisateur, sans `BYPASSRLS`, propriétaire de rien) ; `::test_every_table_is_global_or_org_protected` | DB |
| AC-46 | `tests/test_authz.py::test_no_permission_spans_organizations` (ensemble des permissions figé, tout passe par l'adhésion) ; `tests/db/test_db_roles.py::test_security_definer_functions_are_whitelisted` ; `::test_no_role_bypasses_rls` | unit, DB |
| AC-47 | `tests/api/test_cross_org_ids.py::test_foreign_ids_look_like_missing_ids[patch_member-delete_member-revoke_invitation]` | API |
| AC-48 | `tests/api/test_audit.py::test_audit_log_records_sequence_without_other_org` | API |
| AC-49 | `tests/api/test_audit.py::test_member_and_guest_cannot_read_audit[member-guest]` | API |
| AC-50 | `tests/api/test_route_protection.py::test_no_route_mutates_audit` ; `tests/db/test_db_roles.py::test_audit_table_is_append_only_for_app` | unit, DB |
| AC-51 | `tests/test_security_log.py::test_login_logout_events_logged_without_secrets` (`caplog` ; recherche des valeurs connues : cookie, CSRF, code, id_token, state, verifier) ; `::test_access_log_redacts_auth_query_strings` | API |
| AC-52 | `frontend/src/App.test.tsx` « shows only the login page to visitors » | UI |
| AC-53 | `frontend/src/App.test.tsx` « switching organization updates name and role-dependent screens » ; `frontend/src/features/organizations/OrganizationSwitcher.test.tsx` | UI |
| AC-54 | `frontend/src/App.test.tsx` « management tabs follow permissions [member-admin] » | UI |
| AC-55 | `frontend/src/App.test.tsx` « returns to login with session-ended message on 401 » ; `frontend/src/lib/api.test.ts` « calls onUnauthenticated on 401 » | UI |
| AC-56 | `tests/infra/test_dev_environment.py::test_db_up_creates_and_migrates_database` (`RUN_INFRA=1`) ; démarrage du serveur et de l'interface par `dev.ps1` vérifié à la main lors de `/spec-verify` (liste de contrôle dans `review.md`) | infra |
| AC-57 | `tests/infra/test_dev_environment.py::test_data_persists_across_restart_and_reset_wipes_it` | infra |
| AC-58 | `scripts/check.ps1` (étape `infra: database` puis pytest) ; `tests/test_no_network.py::test_outbound_network_is_blocked` ; `tests/db/test_db_roles.py::test_test_database_is_postgres_with_forced_rls` | API, DB |
| AC-59 | `tests/api/test_organizations.py::test_owner_renames_organization` ; `::test_non_owner_cannot_rename[admin-member-guest]` | API |
| AC-60 | `tests/api/test_invitations.py::test_invitation_sends_no_email_and_appears_at_next_login` (aucune requête sortante ; pas d'`smtplib` dans `app/`) ; `::test_same_domain_without_invitation_gets_no_membership` | API |
| AC-61 | `tests/api/test_members.py::test_owner_invites_second_owner` | API |
| AC-62 | `tests/api/test_members.py::test_ownership_transfer_promote_then_leave` | API |
| AC-63 | `tests/api/test_members.py::test_owner_demotes_coowner_then_cannot_demote_self_as_last_owner` | API |

Tests complémentaires sans AC dédié : `tests/db/test_migrations.py::test_upgrade_downgrade_roundtrip` ; `tests/auth/test_google.py` (validation de l'ID token, cache JWKS) ; tests unitaires des services (règles `can_change_role`).

---

## Dépendances nouvelles

Python (`uv add`, à l'implémentation). Versions vérifiées sur PyPI le 2026-11-24 :

| Paquet | Version | Justification |
|---|---|---|
| `psycopg[binary]` | ≥ 3.3.9 | Pilote Postgres. `binary` embarque libpq, donc rien à installer sous Windows. Requis plus tard par le checkpointer et `procrastinate`. |
| `sqlalchemy[asyncio]` | ≥ 2.1.9 | Requêtes Core paramétrées, moteur et pool async. L'extra `asyncio` tire `greenlet` (non installé par défaut en 2.1). |
| `alembic` | ≥ 1.20.1 | Migrations versionnées (tables, RLS, droits). |
| `pyjwt[crypto]` | ≥ 2.15.1 | Vérification RS256 de l'ID token Google. Déjà présent en dépendance transitive de dev (2.15.1, API `jwt.decode` / `PyJWKSet.from_dict` vérifiée). Il devient une dépendance directe. |
| `httpx` | ≥ 0.28.1 | Passe de dev à exécution : appels à Google, avec un `MockTransport` en test. |

Dev : **aucune** (ni testcontainers, ni respx, ni time-machine, ni pytest-socket). Frontend : **aucune**.

Écartées : `authlib` (voir T8), `itsdangerous` (pas de cookie signé), `email-validator`, `asyncpg`.

---

## Risques et mitigations

| Risque | Mitigation |
|---|---|
| **psycopg async et la boucle Proactor de Windows** (vérifié : `InterfaceError`) | `--loop app.core.aio:selector_loop_factory`, `loop_factory` dans `TestClient`, tests SQL en synchrone, erreur explicite au démarrage. À documenter dans `backend/CLAUDE.md`. |
| Piège de la valeur `''` sur une connexion réutilisée | `NULLIF` dans `app_current_org()` et `app_current_user()`, plus un test dédié sur une connexion réutilisée. |
| Le RLS ne protège pas contre du SQL arbitraire (`set_config` appelable par l'attaquant) | Requêtes uniquement paramétrées, revue. Limite écrite dans l'ADR-0003. |
| Les fonctions `SECURITY DEFINER` sont une exception à « aucun contournement » | Rôle propriétaire sans `BYPASSRLS` et avec ses propres politiques limitées à l'utilisateur courant, `search_path` figé, liste blanche testée. À valider par l'utilisateur (remarque R1). |
| Refus de permission non journalisé à cause du `ROLLBACK` | `record_denial` dans une transaction séparée, testé dans AC-20, AC-33 et AC-48. |
| Course entre deux Propriétaires qui se rétrogradent mutuellement | `SELECT … FOR UPDATE` sur les lignes des Propriétaires, plus un test à deux connexions. |
| Image Postgres 18 : chemin du volume changé (`/var/lib/postgresql`) | À vérifier à l'implémentation. Le test infra AC-57 le détecte. |
| Cloud SQL (spec 009) : rôles, `FORCE RLS`, fonctions `SECURITY DEFINER`, version 18 | Non vérifiable aujourd'hui. Point à contrôler dans la 009, noté dans l'ADR-0003. |
| Cookie `SameSite=Lax` non envoyé si le frontend et l'API sont sur deux domaines en production | Même domaine imposé (proxy en dev, routage par chemin en cible), noté pour la 009. |
| Configuration Google locale (client OAuth, URI de redirection) | Prérequis documentés dans le README. Message d'erreur clair (`login_error=unavailable`) si les identifiants manquent. |
| La table `users` n'a pas de RLS : un bug pourrait lister tous les utilisateurs de la plateforme (leurs domaines d'email trahissent des clients) | Accepté pour la 001 (FR-33 la déclare globale). Les routes ne lisent `users` qu'en jointure avec des adhésions filtrées. Option ultérieure : politique « moi ou membres de l'organisation active » (remarque R7). |
| Pas de limitation de débit sur `/auth/login` et le callback | Hors périmètre de la spec. Les lignes `login_requests` expirées sont purgées à chaque `/login`. À traiter au déploiement (009). |
| Durée de la suite de tests (environ 150 tests API avec base) | `TRUNCATE` (rapide) plutôt que recréer la base ; un moteur par client de test ; matrice AC-27 regroupée par rôle. |
| `--reload` d'uvicorn et pool de connexions | Moteur créé dans le lifespan et libéré (`dispose`) à l'arrêt. |
| Changement de contrat 000 : `thread_id` doit être un UUID connu | Signalé (remarque R3). Le frontend utilise déjà le `thread_id` renvoyé par le serveur. |

---

## Remarques sur la spec

Points trouvés en planifiant. La spec n'a pas été modifiée, à part son statut.

> **Validé par l'utilisateur le 2026-11-24** : R1 à R12 sont acceptées telles que décrites ci-dessous (R7 : à durcir dans une spec ultérieure ; R8 : tests infra à la demande avec `RUN_INFRA=1`).

- **R1 (FR-36)** : « mes organisations » et « mes invitations » demandent une lecture qui traverse les organisations. Le plan l'obtient par trois fonctions `SECURITY DEFINER` dont le propriétaire **reste** soumis au RLS, avec des politiques limitées aux données de l'utilisateur courant. Je ne le considère pas comme un contournement de l'isolation, mais c'est une interprétation de FR-36 à valider explicitement.
- **R2 (FR-38)** : une tentative sur une organisation dont on n'est **pas** membre est écrite au journal **plateforme** (`organization_access_denied`), pas au journal de cette organisation. Sinon les Admins d'une organisation verraient l'activité de personnes extérieures, et le journal pourrait être pollué de l'extérieur. Seuls les refus « dans l'organisation » (membres) vont au journal d'audit.
- **R3 (spec 000, FR-3)** : un `thread_id` fourni par le client et inconnu renvoyait jusqu'ici une nouvelle conversation. Il renverra désormais 404, et il doit être un UUID. Ce n'est couvert par aucun AC de la 000, mais c'est un changement de contrat.
- **R4 (FR-27)** : la lettre de FR-27 empêche un Admin de modifier **un** Admin, lui compris. Il ne peut donc pas se rétrograder lui-même en Membre, mais il peut quitter l'organisation. À confirmer.
- **R5 (FR-18 à FR-21)** : la spec ne dit pas ce qui se passe si l'on invite deux fois la même adresse dans la même organisation. Le plan répond 409 `invitation_already_pending` tant qu'une invitation non expirée existe (il faut la révoquer pour en créer une autre). Une nouvelle invitation après un refus ou une expiration est permise.
- **R6 (FR-19)** : la comparaison des emails ignore seulement la casse, comme écrit. Les alias Gmail (points, `+tag`) ne sont pas normalisés : `b.ob@gmail.com` et `bob@gmail.com` sont traités comme deux adresses différentes.
- **R7 (FR-16, FR-33)** : la table `users` est globale et sans RLS. La confidentialité de la liste des utilisateurs (et donc des domaines clients) repose sur le code seul. À accepter, ou à traiter par une spec ultérieure.
- **R8 (AC-56)** : « le serveur et l'interface démarrent » est vérifié à la main (fenêtres lancées par `dev.ps1`). La partie base de données et migrations est automatisée, mais à la demande (`RUN_INFRA=1`). L'affirmation « tous les critères sont automatisables » n'est donc vraie qu'en partie pour AC-56.
- **R9 (FR-25)** : `/docs` et `/openapi.json` de FastAPI sont des fonctions exposées que la spec ne liste pas. Le plan les sert seulement en `ENVIRONMENT=dev` : en test et en production, la liste d'exemptions est exactement « santé + connexion ».
- **R10 (FR-15, US-6)** : avec l'en-tête `X-Organization-Id`, « l'organisation active » est un choix de l'onglet, revérifié à chaque requête, et non un état de la session. Deux onglets peuvent donc être sur deux organisations différentes. Ça me semble conforme à l'esprit de la spec (aucun mélange), mais c'est un comportement visible.
- **R11 (AC-59)** : « le nouveau nom est affiché à tous ses membres » s'entend à leur prochain chargement. Il n'y a pas de notification en direct.
- **R12 (FR-32)** : les conversations d'un membre retiré restent en base (inaccessibles), et leurs checkpoints disparaissent au redémarrage (`InMemorySaver`). Leur conservation et leur effacement ne sont pas traités, comme le prévoit la spec.

---

## ADR créés

- **[ADR-0003](../../docs/adr/0003-persistance-et-contexte-de-securite.md) — Persistance Postgres et contexte de sécurité par requête** (statut : **accepté**, 2026-11-24). Il couvre psycopg 3 + SQLAlchemy Core + Alembic, les trois rôles Postgres et `FORCE RLS`, `set_config` par transaction et `NULLIF`, les fonctions `SECURITY DEFINER` limitées à l'utilisateur courant, l'en-tête `X-Organization-Id` et l'OIDC sans Authlib.

## Découpage indicatif (pour `/spec-tasks`)

1. Infra : compose, script d'initialisation, Alembic, tables, RLS, tests DB d'isolation et de rôles (AC-42 à 46, 50, 58).
2. Socle : `Settings`, horloge, erreurs, `RequestDb`, `authz.py`, contrôle des routes (AC-28 à 30).
3. Authentification : faux Google, provider, `login_requests`, sessions, CSRF, journal plateforme (AC-1 à 13, 51).
4. Organisations, invitations, membres, audit (AC-14 à 27, 31 à 37, 47 à 49, 59 à 63).
5. Agents et conversations (AC-38 à 41).
6. Frontend (AC-52 à 55 et volets UI de AC-12, 18, 38, 39).
7. Scripts dev et reset, tests infra, documentation (AC-56, 57).
