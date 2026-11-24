# Constitution du projet Forge

Principes non négociables. Chaque `plan.md` doit indiquer explicitement qu'il les respecte (section « Conformité constitution »). On ne modifie ce document qu'avec un ADR.

## I. La spec d'abord
Aucune fonctionnalité sans `spec.md` validée par l'humain. La spec décrit le comportement observable et les critères d'acceptation, pas l'implémentation.

## II. Tests d'abord (TDD)
Pour chaque tâche, on écrit d'abord le test qui échoue, puis le code qui le fait passer, puis on refactorise. Chaque critère d'acceptation est couvert par au moins un test automatisé.

## III. Les tests n'appellent jamais un vrai LLM
Les tests unitaires et d'API utilisent un modèle factice (`tests/conftest.py::fake_model`). Le comportement qui dépend vraiment du modèle (choix d'outil, suivi du prompt) se teste dans `tests/live/` (marqueur `live`) : des tests peu nombreux, peu coûteux et lancés à la demande.

## IV. Agents déterministes autour d'un cœur probabiliste
- Les outils (`tools.py`) sont des fonctions pures et typées, testables sans LLM.
- Les prompts vivent dans `prompts.py`, versionnés, jamais construits dynamiquement à partir de données non fiables sans délimitation.
- Toute action à effet de bord irréversible (écriture externe, paiement, envoi d'email) passe par une validation humaine (`interrupt_on` de deepagents ou `interrupt()` de LangGraph).

## V. Un seul chemin vers le modèle
Tout modèle est construit par `app.core.llm.get_chat_model()`. Pas de clé API, de nom de modèle ni de paramètre de génération en dur ailleurs.

## VI. Contrat API explicite
Les schémas Pydantic sont la source de vérité. Le frontend ne consomme que des endpoints documentés dans le plan de la spec concernée. Le streaming suit le protocole SSE décrit dans `docs/architecture.md`.

## VII. Simplicité
On choisit la solution la plus simple qui satisfait la spec. Une nouvelle dépendance, une nouvelle couche ou un nouveau service exige une justification dans le plan, et un ADR si le choix est structurant. Pas de code « pour plus tard ».

## VIII. Observabilité
Les erreurs d'agent sont renvoyées au client (événement SSE `error`) et journalisées. Le traçage LangSmith peut être activé par variables d'environnement, sans modification de code.

## IX. Sécurité
Aucun secret dans le code ni dans les specs. Les entrées utilisateur sont validées par Pydantic. Le backend de fichiers de deepagents reste virtuel (`StateBackend`) sauf ADR contraire. Les agents n'ont pas d'accès shell réel par défaut.

## X. Isolation des organisations
*Ajouté par l'[ADR-0002](adr/0002-architecture-rag-multi-organisations.md), D1.*

Toute table métier porte une colonne `organization_id` et elle est protégée par une politique RLS de Postgres. Chacune de ces tables a un **test de fuite** : un utilisateur de l'organisation A ne voit jamais une ligne de l'organisation B, même par une requête écrite sans filtre. Le RLS est le filet de sécurité, pas la seule protection : le code filtre aussi explicitement.

## XI. Aucune élévation de privilèges
*Ajouté par l'[ADR-0002](adr/0002-architecture-rag-multi-organisations.md), D3, D4 et D12.*

- Un agent, une base de connaissances ou un admin n'accède aux données qu'avec les droits de la personne qui agit, jamais plus.
- Pour un document venu de Drive, il faut à la fois les droits sur la base de connaissances **et** les droits Drive. L'un ne suffit jamais sans l'autre.
- Le filtrage des droits se fait **avant** le LLM. Un passage non autorisé n'entre jamais dans le contexte du modèle.
- Le code vérifie des **permissions** (`require("kb:read", kb_id)`), jamais des noms de rôles. Une route sans vérification de permission est refusée par défaut, et un test le contrôle.

## XII. Les documents sont des données, pas des instructions
*Ajouté par l'[ADR-0002](adr/0002-architecture-rag-multi-organisations.md), D13.*

Le contenu des documents est toujours délimité dans les prompts et n'est jamais exécuté. Un agent qui a accès à des documents privés n'a aucun outil capable de faire sortir des données (requête web, envoi d'email, écriture externe) sans validation humaine. L'interface n'affiche pas d'images distantes dans les réponses.
