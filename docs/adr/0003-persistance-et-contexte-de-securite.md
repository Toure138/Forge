# ADR-0003 : Persistance Postgres et contexte de sécurité par requête

- **Statut** : accepté (2026-11-24, avec le plan de la spec 001)
- **Date** : 2026-11-24
- **Spec liée** : specs/001-organisations-auth-rbac
- **Précise** : ADR-0002 (D1, D2, D3, D14). Ne remet en cause aucune de ces décisions : il fixe *comment* elles sont mises en œuvre.

## Contexte

La spec 001 introduit la première base de données de Forge. L'ADR-0002 a décidé du *quoi* : Postgres partagé, `organization_id` + RLS avec `SET LOCAL app.organization_id` (D1), connexion Google en BFF avec session serveur et CSRF (D2), permissions vérifiées par `require()` (D3), Docker en local (D14). Il laisse ouverts des choix qui engagent toutes les specs suivantes :

1. la pile d'accès à la base (pilote, couche SQL, migrations) ;
2. les rôles Postgres et la façon dont le RLS est rendu impossible à contourner par l'application ;
3. la façon de lire des données « à moi » qui traversent plusieurs organisations (« mes organisations », « mes invitations ») sans affaiblir l'isolation ;
4. la façon dont le navigateur désigne l'organisation active ;
5. la mise en œuvre d'OIDC (bibliothèque ou code maison).

Contrainte forte : le poste de développement est sous Windows.

## Décision

1. **Pile base de données** : `psycopg` 3 (pilote, API sync et async), **SQLAlchemy 2 Core** en mode async (pas l'ORM), **Alembic** pour les migrations. Les politiques RLS, les droits (`GRANT`) et les fonctions SQL sont écrits en SQL explicite dans les migrations Alembic (`op.execute`), jamais générés.
2. **Trois rôles Postgres** :
   - `forge_owner` : propriétaire du schéma et des tables, utilisé **uniquement** par les migrations ;
   - `forge_app` : rôle de connexion de l'API, `NOSUPERUSER NOBYPASSRLS`, propriétaire de rien, avec des droits table par table (pas d'`UPDATE`/`DELETE` sur le journal d'audit) ;
   - `forge_self_reader` : rôle `NOLOGIN NOBYPASSRLS`, propriétaire de quelques fonctions `SECURITY DEFINER` en lecture seule (voir point 3).
   Toutes les tables d'organisation ont `ENABLE` **et** `FORCE ROW LEVEL SECURITY`.
3. **Lectures « à moi » transverses** : une petite liste fermée de fonctions `SECURITY DEFINER` (`app_my_memberships()`, `app_my_pending_invitations(now)`, `app_my_invitation(id)`), propriété de `forge_self_reader`. Ce rôle **ne contourne pas** le RLS : il a ses propres politiques, qui ne laissent voir que les lignes de l'utilisateur courant (ses adhésions, les invitations adressées à son email, et les noms des organisations correspondantes). Les politiques d'organisation sont, elles, réservées à `forge_app` (`TO forge_app`).
4. **Contexte par transaction** : une transaction par requête HTTP. Au début, l'API exécute `set_config('app.user_id', …, true)` et, pour une route d'organisation, `set_config('app.organization_id', …, true)` (équivalent paramétrable de `SET LOCAL`). Les politiques lisent ces valeurs via `app_current_org()` / `app_current_user()`, qui renvoient `NULL` si la variable est absente **ou vide**.
5. **Organisation active désignée par l'en-tête `X-Organization-Id`**, revérifiée à chaque requête par une lecture de l'adhésion sous RLS. Le serveur ne mémorise pas d'organisation active.
6. **OIDC écrit dans Forge** avec `httpx` (appels à Google) et `PyJWT[crypto]` (vérification de l'ID token), derrière une interface `IdentityProvider`. Pas d'Authlib.

## Alternatives considérées

| Option | Pour | Contre |
|---|---|---|
| `asyncpg` au lieu de `psycopg` 3 | le plus rapide, fonctionne avec la boucle Proactor de Windows sans réglage | API async seulement (migrations et tests SQL plus lourds) ; le checkpointer Postgres de LangGraph et `procrastinate` (ADR-0002 D6) exigent `psycopg` 3 : on aurait deux pilotes dès la spec 003 |
| ORM SQLAlchemy (`AsyncSession`) | modèles riches, relations | carte d'identité, chargements implicites interdits en async, unité de travail cachée : trop de magie pour une spec dont le cœur est du SQL (RLS) |
| SQL brut + exécuteur de migrations maison | transparence totale | on réinvente Alembic (ordre, historique, retour arrière) |
| Créer les rôles dans une migration | tout au même endroit | exige `CREATEROLE` pour le rôle de migration, et les rôles dépendent de l'environnement (Docker en local, Cloud SQL en cible) |
| Fonctions `SECURITY DEFINER` possédées par un rôle `BYPASSRLS` | classique | un rôle qui contourne tout le RLS : exactement ce que FR-36 veut éviter ; certains Postgres managés le restreignent |
| Politique « je vois mes propres adhésions » ouverte à `forge_app` | pas de fonction | une requête sans filtre dans le contexte d'Acme renverrait aussi les adhésions de l'utilisateur à Globex (viole AC-42) |
| Organisation active stockée dans la session | aucun en-tête à envoyer | partagée entre onglets : un onglet affiché sur Acme pourrait écrire dans Globex après un changement dans un autre onglet (mélange de données) |
| Organisation active dans le chemin (`/api/orgs/{id}/…`) | URLs explicites | change tous les chemins de la spec 000 ; même garantie que l'en-tête |
| Authlib | bibliothèque OIDC complète | `authlib.jose` est déprécié (retrait annoncé en 2.0, migration vers `joserfc`) ; l'intégration Starlette impose `SessionMiddleware` (second cookie signé, `itsdangerous`) ; masque les étapes que la spec veut faire apprendre |
| `google-auth` (`verify_oauth2_token`) | officiel Google | synchrone (transport `requests`), ne couvre pas l'échange du code |

## Conséquences

- **Windows** : `psycopg` async refuse la boucle `ProactorEventLoop` (vérifié dans `psycopg/connection_async.py`, 3.3.9). L'API est lancée avec `--loop app.core.aio:selector_loop_factory`, et les tests passent `backend_options={"loop_factory": asyncio.SelectorEventLoop}` au `TestClient`. Sur Linux (Docker, Cloud Run), rien ne change.
- **Toute nouvelle table d'organisation** doit, dans sa migration : porter `organization_id`, activer et forcer le RLS, créer une politique `TO forge_app`, accorder les droits minimaux à `forge_app`. Un test d'introspection échoue sinon (constitution X).
- **Ajouter une fonction `SECURITY DEFINER`** exige de mettre à jour la liste blanche testée et de justifier le besoin dans le plan de la spec.
- **Limite assumée** : le contexte RLS est posé par l'application. Il protège contre les oublis de filtre et les erreurs de logique, pas contre un attaquant qui exécute du SQL arbitraire avec le rôle `forge_app` (il pourrait appeler `set_config`). La parade à ce risque-là reste les requêtes paramétrées (SQLAlchemy), sans exception.
- **Cible GCP (spec 009)** : vérifier que Cloud SQL accepte la création des rôles, `FORCE ROW LEVEL SECURITY` et les fonctions `SECURITY DEFINER` telles que décrites ; garder le frontend et l'API sous le même domaine, sinon le cookie `SameSite=Lax` n'est pas envoyé par `fetch`.
- La persistance des conversations (checkpointer Postgres) n'est **pas** décidée ici : la spec 001 garde `InMemorySaver` ; la spec 003 tranchera, avec `psycopg` déjà en place.
