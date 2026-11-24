# Plan technique : spec NNN — <Titre>

> Le comment. Écrit après validation de `spec.md`. Toute décision structurante → ADR dans `docs/adr/`.

## Résumé de l'approche
En cinq lignes maximum.

## Conformité constitution
| Principe | Respecté ? | Note |
|---|---|---|
| I. Spec d'abord | ✅ | |
| II. TDD | ✅ | |
| III. Pas de vrai LLM en test | ✅ | |
| IV. Agents déterministes | ✅ | |
| V. Un seul chemin vers le modèle | ✅ | |
| VI. Contrat API | ✅ | |
| VII. Simplicité | ✅ | |
| VIII. Observabilité | ✅ | |
| IX. Sécurité | ✅ | |
| X. Isolation des organisations | ✅ | |
| XI. Aucune élévation de privilèges | ✅ | |
| XII. Documents = données | ✅ | |

## Conception de l'agent (si applicable)
- Type : `create_deep_agent` | graphe LangGraph sur mesure (`StateGraph`) | agent simple (`create_agent`)
- Diagramme du graphe (nœuds, arêtes, conditions) :
  ```mermaid
  graph TD
    START --> agent --> END
  ```
- État (`state_schema`), s'il est étendu :
- Outils : nom, signature, effets de bord, gestion d'erreur
- Sous-agents : nom, rôle, outils
- Prompt système : intention et contraintes clés (le texte final ira dans `prompts.py`)
- Human-in-the-loop (`interrupt_on`) :
- Mode de sortie structurée (`response_format`) :

## Contrat API
| Méthode | Chemin | Requête | Réponse | Erreurs |
|---|---|---|---|---|
Schémas Pydantic nouveaux ou modifiés, et nouveaux événements SSE.

## Frontend
Composants, hooks, états (chargement, erreur, vide) et parcours utilisateur.

## Données et persistance
Nouvelles données, stockage et migration.

## Fichiers impactés
| Fichier | Action (créer/modifier) | Raison |
|---|---|---|

## Stratégie de test
| Critère | Test (fichier::nom) | Type (unit/API/UI/live) |
|---|---|---|
| AC-1 | | |

## Dépendances nouvelles
Paquet, version et justification. Rien si possible.

## Risques et mitigations

## ADR créés
