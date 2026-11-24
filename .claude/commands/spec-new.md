---
description: "SDD étape 1 : créer une nouvelle spec à partir d'une idée"
argument-hint: <description de la fonctionnalité ou de l'agent>
---

Étape 1 du workflow SDD (voir CLAUDE.md) : **spécifier**.

Idée de l'utilisateur :
<idee>
$ARGUMENTS
</idee>

1. Si l'idée est vide ou trop vague pour écrire ne serait-ce que l'objectif, pose deux ou trois questions et arrête-toi.
2. Sinon, délègue au sous-agent `spec-writer` en lui transmettant l'idée telle quelle, plus le contexte utile de la conversation.
3. Relis la spec produite : vérifie que chaque AC est testable et qu'elle ne contient aucun détail d'implémentation.
4. Présente à l'utilisateur le chemin de la spec, un résumé, puis les questions `[À CLARIFIER]` numérotées.
5. **Arrête-toi.** Attends que l'utilisateur réponde aux questions ou valide la spec. Intègre ses réponses dans la spec. L'étape suivante est `/spec-plan NNN`, et seulement quand il le demande.
