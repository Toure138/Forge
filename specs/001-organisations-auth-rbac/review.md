# Revue : spec 001 — Organisations, connexion Google, rôles d'organisation et isolation des données

- **Date** : 2026-11-24
- **Relecteur** : sous-agent `reviewer` (lecture seule ; seul ce fichier a été écrit)
- **Branche** : `feat/001-organisations-auth-rbac`, commits 9cbfcd8 → fb8ffd8 depuis a30fe00 (148 fichiers, +16 470 / −271)
- **`scripts/check.ps1`** (lancé par moi, `powershell -NoProfile -File scripts/check.ps1`) : **ALL CHECKS PASSED**, code 0. ruff format (104 fichiers), ruff check, mypy (52 fichiers), `infra: database` Healthy, **pytest 508 réussis + 4 sautés (2 `live`, 2 `infra`) en 105 s**, oxlint, tsc, **vitest 85 tests (16 fichiers)**.
- **`RUN_INFRA=1 uv run pytest -m infra`** (lancé par moi) : **2 réussis** en 50,7 s. Après coup, le conteneur `forge-db-1` tourne toujours (même instance, healthy), le volume `forge_pgdata` est intact et il ne reste aucun conteneur ni volume `forge-smoke`.
- **Verdict : APPROUVÉ AVEC RÉSERVES**

### Justification du verdict

Aucun problème bloquant. Les 63 critères ont chacun au moins un test automatisé vert. J'ai lu les tests des AC de sécurité : ils prouvent bien le critère, avec des témoins négatifs, des comparaisons octet pour octet et des contrôles par mutation notés au Journal. L'isolation repose sur un RLS forcé, testé table par table contre Postgres 18. Le rôle applicatif ne possède rien et n'a pas `BYPASSRLS`. Le contrôle de refus par défaut des routes est automatique.

Trois réserves empêchent de passer en `done` tout de suite :

1. **La vérification manuelle de T054 n'est pas faite** : connexion Google réelle, chat en flux, invitation d'un second compte. C'est aussi le volet manuel d'AC-56. T054 et « Fin de phase 7 » restent décochées.
2. **Majeur M1** : la validation de l'ID token n'admet aucune dérive d'horloge (`iat <= now`). Avec le vrai Google, la connexion peut échouer de façon intermittente si l'horloge du poste retarde d'une fraction de seconde. Le faux Google ne peut pas le révéler. **À corriger avant le test manuel**, sinon il risque d'échouer sans cause visible.
3. **Majeur M2** : `POST /agents/{name}/invoke` garde la transaction ouverte pendant l'appel au LLM. Cette transaction verrouille la ligne `sessions` de l'utilisateur, qui vient d'être mise à jour par `touch`. La limite est notée au Journal, mais sa conséquence est plus large que « une connexion retenue ». Le frontend n'utilise pas `invoke`, donc ce n'est pas bloquant. La correction est petite.

---

## Critères d'acceptation

Légende : ✅ couvert, vert et vérifié par lecture · ⚠️ partiel · ❌ non couvert. Les tests marqués « (lu) » ont été lus en entier. Les autres ont été vérifiés par leur nom, leur docstring et un échantillon.

| AC | Test(s) | Statut | Remarque |
|---|---|:-:|---|
| AC-1 | `tests/auth/test_login.py::test_login_succeeds_for_workspace_and_gmail_accounts[workspace-gmail]` | ✅ | Faux Google. Le comportement réel reste à voir à la main (T054). |
| AC-2 | `tests/auth/test_login.py::test_login_refused_when_email_not_verified` (lu) | ✅ | Aucune session, aucun utilisateur, aucun cookie ; `login_failed/email_not_verified` journalisé. Volet unitaire : `test_google.py::test_unverified_email_is_rejected`. |
| AC-3 | `tests/auth/test_login.py::test_relogin_with_changed_email_keeps_same_account` ; `test_users_service.py::test_upsert_user_by_google_sub_updates_profile` | ✅ | |
| AC-4 | `tests/auth/test_login.py::test_authorization_request_asks_only_basic_identity` ; `test_google.py::test_authorization_url_requests_basic_identity_with_pkce` | ✅ | Scope exact, PKCE S256, sans `access_type=offline` (`app/auth/google.py:19`). |
| AC-5 | `tests/auth/test_login.py::test_callback_rejected[missing_cookie-state_mismatch-replayed-nonce_mismatch-bad_audience-bad_issuer-bad_signature-expired_request]` (lu) ; `::test_state_mismatch_burns_the_login_request` (lu) | ✅ | Le rejeu est testé avec une copie du cookie `forge_login` replacée dans le navigateur. Le nombre de sessions est vérifié en base. Une demande expirée est journalisée `replayed` (voir m3). |
| AC-6 | `tests/auth/test_login.py::test_login_response_has_no_google_token_and_hardened_cookie` (lu) | ✅ | Recherche des jetons réellement émis (ID et accès) dans le corps et les en-têtes. Cookie `HttpOnly`, `Secure`, `Lax`, `Path=/`, sans `Domain`. |
| AC-7 | `tests/auth/test_login_paths.py::test_session_creation_only_from_google_callback` ; `::test_no_fake_identity_provider_in_app_package` | ✅ | Garde AST vue rouge sur des extraits synthétiques et par mutation réelle (Journal T023). |
| AC-8 | `tests/auth/test_sessions.py::test_session_idle_timeout[23h59-ok-24h01-refused]` (lu) | ✅ | |
| AC-9 | `tests/auth/test_sessions.py::test_session_absolute_timeout_despite_activity` (lu) | ✅ | Une requête toutes les 12 h, refus à 7 jours pile. |
| AC-10 | `tests/auth/test_sessions.py::test_logout_revokes_server_side_session` (lu) | ✅ | Copie du cookie présentée après la déconnexion : 401. `revoked_at` vérifié en base. |
| AC-11 | `tests/auth/test_login.py::test_pre_login_session_id_never_authenticated` (lu) | ✅ | Le cookie planté n'existe pas en base. Une seconde connexion révoque la première session. |
| AC-12 | `tests/auth/test_csrf.py::test_every_mutating_route_requires_csrf[…]` (lu, paramétré sur toutes les routes non sûres × manquant/faux) ; `::test_wrong_csrf_token_changes_nothing` ; `::test_csrf_token_is_checked_against_the_session` ; `frontend/src/lib/api.test.ts` « adds X-CSRF-Token on unsafe methods » | ✅ | Le balayage utilise un `X-Organization-Id` aléatoire. « Rien n'est modifié » n'est vérifié que sur les tables globales. Pour les tables d'organisation, c'est la conception qui le garantit : le CSRF est contrôlé avant toute écriture. Voir la suggestion s5. |
| AC-13 | `tests/auth/test_sessions.py::test_authorized_request_works_when_identity_provider_down` (lu) | ✅ | Aucune requête vers Google. |
| AC-14 | `tests/api/test_organizations.py::test_create_organization_makes_creator_sole_owner` (lu) | ✅ | |
| AC-15 | `tests/api/test_organizations.py::test_switch_active_organization_applies_new_role` (lu) | ✅ | |
| AC-16 | `tests/api/test_organizations.py::test_cannot_activate_organization_without_membership` (lu) | ✅ | Réponse identique octet pour octet à celle d'un uuid inconnu. `organization_access_denied` au journal plateforme, rien au journal de Globex. |
| AC-17 | `tests/api/test_organizations.py::test_list_my_organizations_shows_only_memberships` (lu) ; `tests/db/test_self_reader_functions.py::test_my_memberships_lists_only_current_user_across_orgs` | ✅ | |
| AC-18 | `tests/api/test_organizations.py::test_user_without_organization_cannot_use_agents` (lu) ; `NoOrganizationScreen.test.tsx` « shows pending invitations and create form » ; `App.test.tsx` « user without organization sees the no-organization screen » | ✅ | |
| AC-19 | `tests/api/test_invitations.py::test_admin_invites_member` | ✅ | |
| AC-20 | `tests/api/test_invitations.py::test_admin_cannot_invite_admin_or_owner[admin-owner]` | ✅ | Refus journalisé (`permission.denied`, cible `invitation`). |
| AC-21 | `tests/api/test_invitations.py::test_invitee_accepts_with_case_insensitive_email` (lu) | ✅ | `Bob@Exemple.com` ; une seconde utilisation donne 404. |
| AC-22 | `tests/api/test_invitations.py::test_other_email_cannot_accept_invitation` (lu) | ✅ | Quatre personnes testées (extérieur, autre organisation, Membre, **Propriétaire**). Réponse identique à un uuid inconnu ; invitation toujours `pending` ; audit inchangé. |
| AC-23 | `tests/api/test_invitations.py::test_invitee_declines_invitation` (lu) | ✅ | |
| AC-24 | `tests/api/test_invitations.py::test_expired_or_revoked_invitation_cannot_be_accepted[expired-revoked]` (lu) ; `::test_expired_invitation_cannot_be_declined` | ✅ | 410 + `invitation.expired_on_use` validé malgré l'erreur. |
| AC-25 | `tests/api/test_invitations.py::test_admin_cannot_revoke_admin_invitation` | ✅ | |
| AC-26 | `tests/api/test_invitations.py::test_inviting_existing_member_is_refused` | ✅ | |
| AC-27 | `tests/api/test_permission_matrix.py::test_role_permission_matrix[role-action]` (96 cas) ; `::test_every_row_of_the_matrix_is_tried` | ✅ | La table FR-23 est recopiée telle quelle et analysée, sans importer `authz.py`. |
| AC-28 | `tests/test_authz.py::test_changing_role_definition_changes_behavior` (lu) | ✅ | `monkeypatch` de `ROLE_PERMISSIONS` seulement, puis appel d'API. |
| AC-29 | `tests/api/test_route_protection.py::test_every_route_declares_permission_or_exemption` (lu) ; `::test_checker_flags_route_without_permission` ; `::test_exemptions_exist_as_routes` ; `::test_agent_routes_declare_agents_use` ; `tests/test_authz.py::test_public_routes_are_health_and_login_only` | ✅ | Le test des exemptions est dans `test_authz.py`, et non dans `test_route_protection.py::test_exemptions_are_health_and_login_only` comme l'écrit le plan (écart de nom seulement). |
| AC-30 | `tests/api/test_route_protection.py::test_anonymous_request_to_protected_route_is_401[…]` (lu, paramétré depuis `iter_route_contexts`) | ✅ | |
| AC-31 | `tests/api/test_members.py::test_demotion_to_guest_takes_effect_on_next_request` (lu) | ✅ | |
| AC-32 | `tests/api/test_members.py::test_removed_member_loses_access_keeps_other_memberships` (lu) | ✅ | |
| AC-33 | `tests/api/test_members.py::test_admin_cannot_modify_admin_or_owner[admin-owner]` (lu) ; `::test_admin_cannot_remove_admin_or_owner[admin-owner]` (lu) | ✅ | Refus journalisés avec la permission manquante. |
| AC-34 | `tests/api/test_members.py::test_owner_promotes_then_demotes_admin` (lu) | ✅ | |
| AC-35 | `tests/api/test_members.py::test_last_owner_cannot_leave`, `::test_last_owner_cannot_be_demoted`, `::test_last_owner_cannot_be_removed` (lus) ; `tests/services/test_memberships.py::test_concurrent_owner_demotions_keep_one_owner` (lu) | ✅ | Trois tests séparés au lieu du test paramétré du plan. Le test de concurrence élargit la fenêtre de course. Mutation `FOR UPDATE` retiré : rouge 5 fois sur 5 ; 20 exécutions vertes (Journal T030). |
| AC-36 | `tests/api/test_members.py::test_member_leaves_organization` (lu) | ✅ | |
| AC-37 | `tests/api/test_members.py::test_no_self_elevation[admin_to_owner-member_to_admin-member_to_owner]` (lu) | ✅ | |
| AC-38 | `tests/test_api.py::test_list_agents`, `::test_invoke_returns_answer_and_thread`, `::test_invoke_runs_custom_tool`, `::test_stream_emits_sse_events`, `::test_unknown_agent_returns_404` (Membre authentifié) ; `ChatPanel.test.tsx` « streams the assistant answer into the conversation » | ✅ | Non-régression automatisée verte. Le flux avec le vrai LLM, dans le navigateur, reste à voir dans le contrôle manuel de T054. |
| AC-39 | `tests/api/test_agents_access.py::test_guest_cannot_list_or_invoke_agents[list-invoke-stream]` ; `ChatPanel.test.tsx` « guest sees no shared agent message », « 403 on agents shows the same message » ; `App.test.tsx` « chat runs in the active organization; a Guest sees the no-agent message » | ✅ | Refus en JSON avant l'ouverture du flux. |
| AC-40 | `tests/api/test_conversations.py::test_other_user_cannot_continue_conversation[member-admin]` (lu) | ✅ | Une seule application, donc un seul checkpointer : le test atteindrait vraiment le checkpoint d'Alice si le contrôle manquait. Réponse identique octet pour octet ; le faux modèle n'est pas appelé. Socle base : `tests/db/test_isolation.py::test_conversation_visible_only_to_its_owner_user`. |
| AC-41 | `tests/api/test_conversations.py::test_conversation_not_reachable_from_other_organization` (lu) | ✅ | |
| AC-42 | `tests/db/test_isolation.py::test_unfiltered_select_sees_only_active_org[5 tables]` (lu) | ✅ | SQL volontairement sans filtre, en `forge_app`. |
| AC-43 | `tests/db/test_isolation.py::test_unfiltered_update_delete_cannot_touch_other_org[5 tables]` (lu) | ✅ | Les écritures permises touchent 1 ligne, celles qui ne sont pas accordées donnent `permission denied`. L'empreinte de Globex est inchangée. |
| AC-44 | `tests/db/test_isolation.py::test_insert_or_move_row_into_other_org_is_rejected[5 tables]` (lu) | ✅ | Insertion et déplacement d'une ligne vers une autre organisation. |
| AC-45 | `tests/db/test_isolation.py::test_no_context_returns_no_rows[…]`, `::test_reused_connection_after_transaction_returns_no_rows` (lus) ; `tests/db/test_request_db.py::test_context_does_not_leak_to_next_transaction_on_pooled_connection` ; `tests/db/test_db_roles.py::test_app_role_cannot_bypass_rls` (lu) | ✅ | Connexion neuve **et** connexion réutilisée (cas `''`). |
| AC-46 | `tests/test_authz.py::test_no_permission_spans_organizations` ; `tests/db/test_db_roles.py::test_security_definer_functions_are_whitelisted` (lu), `::test_no_role_bypasses_rls`, `::test_self_reader_is_read_only` (lu) | ✅ | |
| AC-47 | `tests/api/test_cross_org_ids.py::test_foreign_ids_look_like_missing_ids[patch_member-delete_member-revoke_invitation × admin-owner]` (lu) | ✅ | Il n'existe pas de route de consultation unitaire : le volet « consulter » est donc sans objet. |
| AC-48 | `tests/api/test_audit.py::test_audit_log_records_sequence_without_other_org` (lu) ; `AuditLogPanel.test.tsx` | ✅ | Auteur, cible, date et résultat de 5 étapes, dont un refus. Aucune trace de Globex. |
| AC-49 | `tests/api/test_audit.py::test_member_and_guest_cannot_read_audit[member-guest]` (lu) | ✅ | |
| AC-50 | `tests/api/test_route_protection.py::test_no_route_mutates_audit` (lu) ; `tests/db/test_db_roles.py::test_audit_table_is_append_only_for_app` (lu) | ✅ | Droits en base (pas d'UPDATE, DELETE, TRUNCATE ni TRIGGER) et garde AST sur `app/`. |
| AC-51 | `tests/test_security_log.py::test_login_logout_events_logged_without_secrets` (lu) ; `::test_redact_filter_masks_auth_query_strings` ; `::test_app_installs_access_log_redaction` ; `::test_sql_parameters_are_never_logged` (lu) | ✅ | Les 16 secrets réels (cookies, CSRF, state, nonce, code, verifier, jetons Google) sont recherchés dans **tous** les enregistrements de tous les loggers. L'email aussi. |
| AC-52 | `App.test.tsx` « shows only the login page to visitors » (lu) ; `LoginPage.test.tsx` | ✅ | |
| AC-53 | `App.test.tsx` « switching organization updates name and role-dependent screens » (lu) ; `OrganizationSwitcher.test.tsx` | ✅ | |
| AC-54 | `App.test.tsx` « management tabs follow permissions [member-guest-admin-owner] » (lu) | ✅ | |
| AC-55 | `App.test.tsx` « returns to login with session-ended message on 401 » (lu) ; `api.test.ts` « calls onUnauthenticated on 401 » ; `AuthProvider.test.tsx` | ✅ | |
| AC-56 | `tests/infra/test_dev_environment.py::test_db_up_creates_and_migrates_database` (`RUN_INFRA=1`, vert) | ⚠️ | Le volet base et migrations est automatisé et vert. Le démarrage du serveur et de l'interface par `dev.ps1` doit être vérifié à la main (R8). Ce contrôle n'est pas fait (T054). |
| AC-57 | `tests/infra/test_dev_environment.py::test_data_persists_across_restart_and_reset_wipes_it` (`RUN_INFRA=1`, vert) | ✅ | Test sur un projet compose séparé ; les données du développeur sont vérifiées intactes. |
| AC-58 | `scripts/check.ps1` (étape `infra: database` puis pytest) ; `tests/test_no_network.py::test_outbound_network_is_blocked` ; `tests/db/test_db_roles.py::test_test_database_is_postgres_with_forced_rls` (lu) | ✅ | Limite : la résolution DNS n'est pas interceptée (Journal T013), ce qui ne change rien à la garantie. |
| AC-59 | `tests/api/test_organizations.py::test_owner_renames_organization`, `::test_non_owner_cannot_rename[admin-member-guest]` (lus) | ✅ | |
| AC-60 | `tests/api/test_invitations.py::test_invitation_sends_no_email_and_appears_at_next_login` (lu), `::test_same_domain_without_invitation_gets_no_membership` (lu) | ✅ | Garde réseau, garde AST `smtplib`/`email`, parcours de connexion complet. |
| AC-61 | `tests/api/test_members.py::test_owner_invites_second_owner` (lu) | ✅ | |
| AC-62 | `tests/api/test_members.py::test_ownership_transfer_promote_then_leave` (lu) | ✅ | |
| AC-63 | `tests/api/test_members.py::test_owner_demotes_coowner_then_cannot_demote_self_as_last_owner` (lu) | ✅ | |

**Bilan : 62 ✅ · 1 ⚠️ (AC-56, volet manuel) · 0 ❌.**

---

## Conformité tâches et plan

- `tasks.md` : T001 à T053 cochées. **T054 et « Fin de phase 7 » ne le sont pas**, ce qui est cohérent avec le Journal : la partie manuelle n'est pas faite.
- Fichiers, contrat API et événements SSE conformes au plan. Les écarts sont tous consignés et justifiés au Journal :
  - pas de `USAGE` sur la séquence d'audit ;
  - `hide_parameters=True` sur le moteur ;
  - `exp`/`iat` vérifiés avec l'horloge injectée ;
  - `authorization_url` asynchrone ;
  - `record_denial(error, …)` et `commit_and_raise` ;
  - routes renommées `invoke_agent` et `stream_agent` ;
  - `agent_not_found` en `ErrorBody` ;
  - `-DbOnly` dans `dev.ps1`.
- Types TS (`frontend/src/lib/api.ts`) : identiques aux schémas Pydantic (`schemas/common.py`, `auth.py`, `organizations.py`, `members.py`, `invitations.py`, `audit.py`, `agents.py`, `errors.py`), vérifiés champ par champ.
- `thread_id` : créé par le serveur, contrôlé avant l'ouverture du flux, clé de checkpoint `org/user/conversation`. Le client le renvoie sans en inventer (`ChatPanel.test.tsx` « reuses the server thread_id »).
- Table de traçabilité du plan : trois noms de test diffèrent de la réalité (AC-29, AC-35, AC-51 ; voir le tableau). C'est sans effet sur la couverture. On pourra les aligner en passant.

---

## Constitution (I–XII) et ADR-0002 / ADR-0003

| Principe | Statut | Constat |
|---|:-:|---|
| I. Spec d'abord | ✅ | Spec validée, sans marqueur. Les écarts sont signalés au Journal sans modifier la spec. |
| II. TDD | ✅ | Le Journal note pour chaque tâche le test vu rouge d'abord, et des contrôles par mutation pour les gardes. |
| III. Pas de vrai LLM | ✅ | `fake_model`, faux Google via `httpx.MockTransport` et garde réseau autouse (hors tests `live`). |
| IV. Agents déterministes | ✅ / N/A | Agents inchangés. Aucune action irréversible : Forge n'envoie pas d'email (garde AST). |
| V. Un seul chemin vers le modèle | ✅ | Aucun `init_chat_model` ni `ChatAnthropic` hors de `app/core/llm.py`. |
| VI. Contrat API | ✅ | Schémas Pydantic et types TS synchronisés ; format d'erreur unique ; protocole SSE inchangé. |
| VII. Simplicité | ✅ | Dépendances Python justifiées : `psycopg`, `sqlalchemy`, `alembic`, `pyjwt` ; `httpx` passe de dev à exécution. Aucune dépendance frontend, pas de routeur, pas d'ORM. |
| VIII. Observabilité | ⚠️ | L'événement SSE `error` est renvoyé, mais **il n'est pas journalisé** : l'exception est interceptée et le commentaire « logged by uvicorn » est faux (`app/api/routes/agents.py:188`). Ce défaut existait déjà en 000, mais il reste un écart à la constitution. Voir m1. |
| IX. Sécurité | ✅ | Secrets uniquement dans `.env` (non versionné ; `.env.example` sans valeur réelle), `SecretStr`, `hide_parameters`, entrées validées par Pydantic. |
| X. Isolation | ✅ | 5 tables d'organisation, `ENABLE` + `FORCE`, politique `TO forge_app`, test de fuite par table, test d'introspection qui refuse aussi les vues. Le code filtre aussi explicitement. Le filtre `user_id` des conversations a été retiré par mutation : le RLS bloque toujours (défense en profondeur, Journal T037). |
| XI. Aucune élévation | ✅ | Hors de `authz.py`, les noms de rôle n'apparaissent que dans les copies testées (`schemas/common.py`, `db/tables.py`) et dans l'invariant `Role.OWNER` (`services/memberships.py:59,79`). Le frontend teste des permissions. `lib/roles.ts` est un miroir réservé à l'affichage. |
| XII. Documents = données | N/A | Aucun document. L'avatar Google n'est pas affiché (aucune image distante). |
| ADR-0002 D1 | ✅ | RLS sur les 5 tables. La table `conversations` correspond à `threads`, comme le plan le note. |
| ADR-0002 D2 | ✅ | BFF : code + PKCE + state + nonce, session serveur, cookie durci, CSRF synchroniseur. |
| ADR-0002 D3 | ✅ | Matrice FR-23 testée en entier. L'admin ne lit pas les conversations (RLS `user_id`). Aucun accès opérateur. |
| ADR-0003 | ✅ | psycopg 3 + Core + Alembic ; 3 rôles ; `set_config(…, true)` + `NULLIF` ; 3 fonctions `SECURITY DEFINER` en liste blanche ; `X-Organization-Id` ; OIDC sans Authlib. |

---

## Revue de sécurité ciblée

**RLS, rôles et `SECURITY DEFINER` (migration 0002).** C'est solide.
- `forge_app` : `NOSUPERUSER`, `NOBYPASSRLS`, propriétaire de rien, membre d'aucun rôle (testé). Ses droits valent exactement la table du plan, sans droit par colonne (testé).
- Le `FORCE` empêche aussi `forge_owner` de lire.
- Les fonctions `SECURITY DEFINER` appartiennent à `forge_self_reader` (`NOLOGIN`, sans `BYPASSRLS`, lecture seule, sans `CREATE`). Elles sont soumises à ses propres politiques limitées à l'utilisateur courant. Leur `search_path` est figé, leurs corps qualifiés `public.`, et `EXECUTE` est retiré à `PUBLIC` dans le bon ordre (piège relevé au Journal T005).
- Les politiques référencent `app_current_org()` par OID : un objet homonyme ne peut pas les détourner.
- Limite connue et documentée : un SQL injecté pourrait appeler `set_config` lui-même. Aucune requête n'est concaténée dans `app/`. Les seuls `f-string` SQL sont dans la migration, sur des constantes.

**Contexte par requête.** `set_config(…, true)` meurt avec la transaction. Une seule instance de `RequestDb` sert toute la requête (alias `RequestDbDep`). Aucune fuite par le pool n'est possible : c'est testé avec un pool de taille 1 et vu rouge avec `set_config(…, false)`. `record_denial` repose bien le contexte après le `ROLLBACK`.

**Autorisation.** `require(perm)` : session → CSRF → en-tête → `set_organization` → adhésion relue à chaque requête (filtre explicite + RLS) → permission. Seul `authz.py` teste des rôles. `can_change_role` exige de pouvoir gérer **le rôle actuel et le rôle visé** : l'auto-élévation est impossible (testé). Limite : les permissions de l'auteur ne sont pas revérifiées sous verrou (m2).

**Flux OIDC.**
- `state` est lié au navigateur par le cookie `forge_login`, et la demande est à usage unique : un seul `UPDATE … RETURNING`, consommée avant tout autre contrôle.
- `nonce` et `state` sont comparés en `compare_digest`. PKCE S256.
- L'ID token est vérifié : signature RS256 contre les JWKS (rechargés une fois si le `kid` est inconnu), `iss` sous ses deux formes, `aud`, `azp`, `exp`/`iat`, `email_verified is True`, `hd` ignoré.
- L'URI de redirection et l'URL de retour viennent de la configuration : pas de redirection ouverte. L'`access_token` est jeté.
- **Faiblesse** : aucune tolérance de dérive d'horloge (M1).
- Fixation de session : il n'existe aucune session avant la connexion. La session présentée est révoquée et une nouvelle est toujours créée (testé).
- Une demande inconnue, déjà utilisée **ou expirée** est journalisée `replayed` (m3).

**Cookies et CSRF.**
- `forge_session` : `HttpOnly; Secure; SameSite=Lax; Path=/`, sans `Domain`. `forge_login` limité à `Path=/api/auth`.
- Le validateur de `Settings` refuse `COOKIE_SECURE=false` en prod.
- Le CSRF est vérifié avant toute écriture, même `last_seen_at`. Aucune route `GET` n'a d'effet métier, à part `/auth/login` (exemptée, n'agit que sur `login_requests`) et `touch`.
- `CORSMiddleware` est toujours installé, sans `allow_credentials` : aujourd'hui une origine tierce ne peut pas lire `/auth/session` avec le cookie. Il est inutile depuis que le proxy rend tout same-origin (m7).

**Journaux sans secret.**
- `security_event` a une liste de champs fermée.
- Le filtre du journal d'accès d'uvicorn masque la query string de `/api/auth/*`.
- `hide_parameters=True` ferme une vraie fuite trouvée en T024.
- Le test de bout en bout cherche les vraies valeurs dans tous les loggers.
- Reste à corriger : le message SSE `error` transmet `str(exc)` au client (m1). Ce n'est pas un secret de Forge, mais c'est un détail technique interne (exigence non fonctionnelle « Sécurité »).

**Indiscernabilité 404.** Elle est vérifiée **octet pour octet** (statut, en-têtes, corps) pour les organisations (AC-16), les membres et invitations (AC-47), les invitations d'autrui (AC-22) et les conversations (AC-40, AC-41). Aucun journal d'audit n'est écrit côté victime ni côté auteur.

**Concurrence dernier Propriétaire.** `FOR UPDATE` sur les lignes des Propriétaires, toujours dans le même ordre et avant la cible (pas d'interblocage). Testé sur deux connexions avec une fenêtre de course élargie. Les invitations concurrentes sont départagées par l'index unique partiel (`ON CONFLICT DO NOTHING`), et l'acceptation par `UPDATE … WHERE status='pending' RETURNING`.

**Frontend.**
- Jeton CSRF en mémoire du module seulement.
- Une seule clé `localStorage` (`forge.activeOrganizationId`), non secrète et revalidée.
- Les messages affichés passent par `messageFor(code)` : testé (« shows translated errors, never the raw server message »).
- Événements SSE validés à la lecture ; aucune image distante.
- Limite connue : jeton CSRF périmé dans un autre onglet (m4).

---

## Problèmes

### Bloquant

Aucun.

### Majeur

- **M1 — Aucune tolérance de dérive d'horloge sur l'ID token Google.** `backend/app/auth/google.py:166-169` (`iat <= now < exp`, aucune marge). Google signe `iat` avec sa propre horloge. Si l'horloge du serveur (poste Windows en local) retarde de plus que la latence réseau, le jeton est « émis dans le futur » et la connexion échoue (`invalid_id_token`, « La connexion a échoué »). C'est l'erreur connue « Token used too early » des bibliothèques Google. Le faux Google partage l'horloge du test, donc il ne peut pas révéler le problème.
  *Correction* : une marge configurable (`OIDC_CLOCK_SKEW_SECONDS`, 60 s par défaut) appliquée à `iat` (`iat <= now + skew`) et éventuellement à `exp`, toujours avec l'horloge injectée. Ajouter des tests : `iat = now + 30 s` accepté, `iat = now + 120 s` refusé, `exp = now - 1 s` refusé (ou accepté dans la marge, selon le choix). **À faire avant le contrôle manuel de T054.** La correction fait moins de 30 lignes et ne change pas le comportement nominal : c'est un correctif hors SDD.

- **M2 — `invoke` garde la transaction et un verrou de ligne pendant l'appel au LLM.** `backend/app/api/routes/agents.py:138-152`, avec `backend/app/auth/dependencies.py:76` (`touch`). La transaction de la requête n'est validée qu'au retour de la fonction de route, donc après `ainvoke`. Le Journal (T037) note la connexion retenue. Mais cette transaction a aussi fait `UPDATE sessions SET last_seen_at` : **la ligne de session reste verrouillée** jusqu'à la fin de l'appel au modèle. Pendant ce temps, toute autre requête de la même session (autre onglet, `GET /auth/session`, liste des membres) bloque sur son propre `touch`. Une dizaine d'`invoke` longs simultanés épuisent aussi le pool (5 + 10 par défaut) et bloquent toute l'API. Ce constat est déduit du code et de la sémantique des verrous de ligne de Postgres ; aucun test ne l'a reproduit.
  *Acceptable pour la 001 ?* Oui provisoirement : le frontend n'utilise que `stream`. Mais `invoke` est un endpoint documenté, et la correction est simple.
  *Correction* : faire comme `stream`. Valider la transaction après `_conversation`, avant `ainvoke`, par exemple avec un `await db.connection.commit()` explicite suivi de la libération de la connexion, ou une dépendance dédiée. Ajouter un test : une requête concurrente de la même session aboutit pendant que le faux modèle est bloqué, et `pool.checkedout() == 0` pendant l'appel (comme `test_no_db_connection_held_during_stream`).

### Mineur

- **m1 — Erreur d'agent non journalisée et texte brut envoyé au client.** `backend/app/api/routes/agents.py:188-189`. `except Exception as exc: yield _sse("error", {"message": str(exc)})`. L'exception n'est écrite dans aucun journal (constitution VIII), et son texte peut contenir des détails internes (message de l'API du fournisseur, chemins), contraire à l'exigence « aucun détail technique interne ». Le frontend affiche un message générique, mais l'API expose le texte. Le défaut vient de la 000.
  *Correction* : `logger.exception(...)` côté serveur, et un message générique (ou un `code`) dans l'événement. Corriger le commentaire. Le protocole SSE de `docs/architecture.md` reste inchangé.
- **m2 — Permissions de l'auteur non revérifiées sous verrou.** `backend/app/services/memberships.py:173-187` et `:225-238`, et `services/invitations.py` : les permissions viennent de `require`, en début de requête. Un Admin rétrogradé ou retiré en même temps peut encore réussir l'action en cours. L'invariant « au moins un Propriétaire » tient. La limite est connue (Journal T030) et **acceptable pour la 001**, car la fenêtre ne dure que quelques millisecondes.
  *Option* : relire l'adhésion de l'auteur `FOR SHARE` après `_lock_owners`.
- **m3 — Une demande de connexion expirée est journalisée `replayed`.** `backend/app/api/routes/auth.py:148-149`. C'est trompeur pour l'analyse d'incident (un rejeu est une attaque, une expiration est banale). **Acceptable pour la 001**.
  *Correction ultérieure* : ajouter la raison `login_request_expired` (une seconde lecture en cas d'échec), plan + `security_log.py`.
- **m4 — Jeton CSRF périmé dans un autre onglet** après une reconnexion. Les écritures y reçoivent 403 `csrf_failed` jusqu'au rechargement de la page (Journal, phase 6). **Acceptable pour la 001**, et le message demande de recharger.
  *Option* : sur `csrf_failed`, relire `GET /auth/session` une fois puis rejouer la requête.
- **m5 — Table `users` sans RLS (R7).** Une requête boguée pourrait lister tous les utilisateurs, et leurs domaines d'email trahissent les clients. Accepté par l'utilisateur (R7) ; les routes ne lisent `users` qu'en jointure avec des adhésions filtrées. **Acceptable pour la 001**, à reprendre dans une spec ultérieure (politique « moi ou membres de l'organisation active »).
- **m6 — Durée de la suite** : 105 s pour pytest (≈ 0,6 s de construction du monde par test d'API). **Acceptable pour la 001.** À surveiller : la 003 ajoutera des tables et des tests de fuite. Piste déjà validée en T038 : écrire le monde en une transaction et partager l'application par module.
- **m7 — `CORSMiddleware` résiduel.** `backend/app/main.py:74-79` et `app/core/config.py:24`. Il est inutile, puisque tout est same-origin via le proxy. Si quelqu'un ajoutait plus tard `allow_credentials=True`, une origine listée pourrait lire `/auth/session`, donc le jeton CSRF.
  *Correction* : le retirer, ou documenter qu'il ne doit jamais autoriser les identifiants.
- **m8 — Traçabilité du plan désynchronisée** pour AC-29, AC-35 et AC-51 (noms de tests, voir le tableau). C'est de la documentation seulement.

### Suggestion

- **s1** — `backend/migrations/versions/0002_row_level_security.py:162` : la documentation de Postgres recommande de placer `pg_temp` explicitement **en dernier** dans le `search_path` d'une fonction `SECURITY DEFINER` (`SET search_path = pg_catalog, public, pg_temp`). Sinon une table temporaire de l'appelant est cherchée en premier. Les corps sont qualifiés `public.`, donc ce n'est pas exploitable aujourd'hui. À ajouter dans une prochaine migration (et dans le test de liste blanche).
- **s2** — `backend/app/auth/dependencies.py:28-37` et `app/auth/sessions.py:109-118` : `Principal` et `ValidSession` exposent `csrf_token` (et le hachage de session) dans leur `repr`. Une trace qui afficherait les variables locales le journaliserait. Ajouter `field(repr=False)`, comme pour `SmokeProject` (Journal T049).
- **s3** — Sur un 401 dû à une session expirée ou révoquée, effacer le cookie `forge_session`. Cela éviterait un `session_rejected` à chaque chargement de page jusqu'à la reconnexion.
- **s4** — Limitation de débit sur `/auth/login` et `/auth/callback`, déjà prévue pour le déploiement (spec 009).
- **s5** — Ajouter un cas CSRF « réaliste » : un Admin d'Acme, avec un `X-Organization-Id` valide, `POST /organization/invitations` sans jeton → 403 et table `invitations` inchangée. Le balayage actuel utilise une organisation aléatoire.

### Limites connues : sont-elles acceptables pour la 001 ?

| Limite (Journal, plan) | Acceptable ? |
|---|---|
| `invoke` garde la connexion DB pendant l'appel LLM (T037) | Oui provisoirement, mais l'effet est plus large que noté (verrou de la ligne de session) : correction recommandée (M2). |
| Jeton CSRF périmé dans un autre onglet (phase 6) | Oui (m4). |
| Table `users` sans RLS (R7) | Oui, accepté par l'utilisateur ; à durcir plus tard (m5). |
| Raison `replayed` pour une demande expirée (T020) | Oui (m3). |
| Durée de la suite ~105–114 s | Oui (m6). |
| Permissions de l'auteur non revérifiées sous verrou (T030) | Oui (m2). |
| Garde réseau sans interception DNS (T013) | Oui : une connexion sortante échoue de toute façon. |
| Reloader d'uvicorn sous Windows (`CTRL_C_EVENT`, T001) | Oui : il fonctionne dans les fenêtres de `dev.ps1`. |
| Pas de limitation de débit sur la connexion (plan) | Oui, prévu en 009 (s4). |

---

## Points ouverts avant `done`

1. **Corriger M1** (marge d'horloge sur l'ID token), de préférence avant le test manuel.
2. **Contrôle manuel de T054 et volet manuel d'AC-56 (R8)**, à faire par l'utilisateur une fois le client OAuth Google créé (« Application Web », URI `http://localhost:5173/api/auth/callback`, comptes de test déclarés sur l'écran de consentement, `GOOGLE_CLIENT_ID` et `GOOGLE_CLIENT_SECRET` dans `.env`). Liste reprise du Journal (T054 et consignes de la phase 7) :
   - [ ] `.\scripts\dev.ps1` sur le poste : base saine, migrations à `head`, backend sur :8000 (`/api/health` = `{"status":"ok"}`), Vite sur :5173.
   - [ ] Ouvrir http://localhost:5173 (même origine que `OIDC_REDIRECT_URI`) : seule la page de connexion s'affiche.
   - [ ] Connexion Google réelle avec un compte Gmail, puis avec un compte Workspace si possible.
   - [ ] Annuler chez Google → message « connexion annulée » (`login_error=cancelled`). Vider temporairement `GOOGLE_CLIENT_ID` → `login_error=unavailable`.
   - [ ] Écran « aucune organisation » → créer une organisation : l'utilisateur en est Propriétaire et l'événement figure au Journal.
   - [ ] Chat **en flux** avec l'agent `assistant` (vrai LLM) ; les messages suivants continuent la même conversation.
   - [ ] Inviter l'adresse d'un second compte Google, se connecter avec ce compte dans un autre profil de navigateur, voir l'invitation, l'accepter.
   - [ ] Changer d'organisation dans deux onglets différents (R10) : chaque onglet garde la sienne.
   - [ ] Invité : onglet Chat avec « Aucun agent ne vous est partagé pour l'instant », sans onglets Membres ni Journal.
   - [ ] Rétrograder un Membre en Invité pendant qu'il discute : son envoi suivant affiche le message FR-45.
   - [ ] Se déconnecter dans un onglet : l'action suivante de l'autre onglet ramène à la connexion avec « Votre session a pris fin ».
   - [ ] Journal de plus de 50 événements : le bouton « Plus ancien » charge la suite.
   - [ ] Navigation au clavier : Tab jusqu'aux onglets, puis flèches, Début et Fin ; sélecteur d'organisation au clavier.
   - [ ] `uv run langgraph dev` démarre toujours (graphes de `studio.py`).
   - Rappel : en dev, `StrictMode` double les requêtes au montage.
3. Cocher T054 et « Fin de phase 7 », noter le résultat au Journal, puis passer la spec en `done`.
4. Recommandé, non bloquant pour `done` : M2 et m1. Le reste peut aller en dette suivie.

### Tâches correctives proposées pour `tasks.md`

Je ne les ai pas ajoutées : la consigne de cette revue interdit de modifier `tasks.md`.

- **T055** `backend` — Marge d'horloge OIDC (M1) : réglage `OIDC_CLOCK_SKEW_SECONDS` (60 s par défaut), appliqué à `iat`/`exp` avec l'horloge injectée ; tests `test_google.py::test_time_claims_tolerate_clock_skew[…]` ; `.env.example`.
- **T056** `backend` — `invoke` sans transaction pendant l'appel LLM (M2) : validation avant `ainvoke` ; tests `test_conversations.py::test_no_db_connection_held_during_invoke` et « une requête concurrente de la même session n'est pas bloquée ».
- **T057** `backend` — Erreurs d'agent journalisées et message générique dans l'événement SSE `error` (m1) ; test `caplog` et absence du texte de l'exception dans le flux.
- (Facultatif) **T058** `backend` — Durcissements s1 à s3 et retrait du `CORSMiddleware` (m7).

## Recommandations

- Faire T055 avant le test manuel ; T056 et T057 dans la foulée (chacune fait moins de 30 lignes, avec un test).
- Pour la 003, reprendre m5 (RLS sur `users`), m3 (raison `login_request_expired`) et s1 (`pg_temp`) dans le plan, avec la règle « toute table d'organisation dans la même migration », déjà écrite dans `backend/CLAUDE.md`.
- Garder les gardes d'introspection et les balayages de routes (401, CSRF, permission) : elles couvriront seules les routes et les tables des specs suivantes.

**Statut** : la spec reste en `review`. Cette revue ne le modifie pas, sur consigne. Il passera en `done` après le point ouvert 2 (et 1), sur décision de l'utilisateur.

---

## Suivi des corrections (2026-11-24)

Les tâches T055 à T057 ont été ajoutées à `tasks.md` (phase 8) avec l'accord de l'utilisateur, puis implémentées en TDD. `check.ps1` → ALL CHECKS PASSED (pytest 519 réussis + 4 sautés, vitest 85), relancé par l'orchestrateur.

| Problème | Tâche | État | Preuve |
|---|---|---|---|
| M1 : aucune marge d'horloge sur `iat` / `exp` | T055 | corrigé | `OIDC_CLOCK_SKEW_SECONDS` (défaut 60, 0 à 300) ; `test_google.py::test_time_claims_tolerate_clock_skew[4 bornes]`, `::test_clock_skew_setting_is_applied`, `test_config.py::test_oidc_clock_skew_is_configurable` |
| M2 : `invoke` garde la transaction (et le verrou de la ligne de session) pendant l'appel au LLM | T056 | corrigé | `RequestDb.release()` avant `ainvoke` ; `test_conversations.py::test_no_db_connection_held_during_invoke`, `::test_concurrent_request_of_same_session_not_blocked_by_invoke` (vus rouges sur l'ancien code) |
| m1 : erreur d'agent non journalisée, `str(exc)` envoyé au client | T057 | corrigé | logger `forge.agents` ; message générique (`error` SSE, 500 `agent_error` pour `invoke`) ; `tests/api/test_agents_errors.py` |

Écart de contrat noté : `POST /agents/{name}/invoke` peut maintenant répondre **500 `agent_error`** (non listé dans la table d'erreurs du plan, ajouté à `docs/architecture.md`). Le frontend connaît déjà ce code et n'appelle pas `invoke`.

Restent ouverts : les mineurs jugés acceptables pour la 001 (m2 à m8), les suggestions s1 à s5 (T058 non retenue), et le **contrôle manuel de T054**, seule condition restante avant `done`.

