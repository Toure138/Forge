---
id: "001"
title: Organisations, connexion Google, rôles d'organisation et isolation des données
status: ready   # draft → planned → ready → in-progress → review → done
created: 2026-11-24
owner: utilisateur
---

# Organisations, connexion Google, rôles d'organisation et isolation des données

> Première étape de la feuille de route de l'[ADR-0002](../../docs/adr/0002-architecture-rag-multi-organisations.md) (section 5). Cette spec applique les décisions D1, D2 et D3 de l'ADR et les principes X et XI de la [constitution](../../docs/constitution.md). Quand l'ADR tranche une question, la spec cite la décision et ne la remet pas en cause.

## Contexte et objectif

Aujourd'hui, Forge n'a ni utilisateurs ni base de données. Les fonctions de liste et d'exécution des agents (spec 000) sont ouvertes à quiconque atteint le serveur (cf. [architecture](../../docs/architecture.md), partie A). C'est acceptable pour un socle local, mais impossible pour une plateforme qui va accueillir les documents de plusieurs clients.

Cette spec pose les fondations de sécurité sur lesquelles toutes les specs suivantes s'appuient :

- **savoir qui parle** : connexion avec un compte Google, qu'il soit personnel (Gmail) ou professionnel (Google Workspace) ;
- **savoir pour quelle organisation il agit** : un utilisateur peut appartenir à plusieurs organisations et choisit celle qui est active ;
- **savoir ce qu'il a le droit de faire** : quatre rôles d'organisation (Propriétaire, Admin, Membre, Invité), traduits en permissions, avec un refus par défaut ;
- **garantir qu'aucune donnée ne passe d'une organisation à une autre**, y compris en cas d'erreur dans le code ;
- **permettre à un développeur de lancer tout l'environnement en local**, base de données comprise, sans rien installer à la main.

Pourquoi maintenant : la sécurité est très difficile à ajouter après coup (ADR-0002, section 5). Toutes les données des specs suivantes (bases de connaissances, documents, agents, conversations) devront naître déjà rattachées à une organisation et déjà protégées.

## Utilisateurs et scénarios

Acteurs :

- **Visiteur** : personne non connectée.
- **Utilisateur** : personne connectée avec Google. Elle peut être membre de zéro, une ou plusieurs organisations, avec un rôle dans chacune.
- **Propriétaire**, **Admin**, **Membre**, **Invité** : rôles d'un utilisateur *dans une organisation donnée* (cf. ADR-0002 D3).
- **Opérateur de la plateforme** : la personne qui exploite Forge. Il n'a aucun accès implicite aux données des organisations (cf. ADR-0002 D3, règle 3).
- **Développeur** : personne qui fait tourner Forge sur son poste.

Scénarios :

- **US-1** : En tant que visiteur, je veux me connecter avec mon compte Google, personnel ou professionnel, afin d'accéder à Forge sans créer de mot de passe.
- **US-2** : En tant qu'utilisateur, je veux que ma session reste ouverte un temps raisonnable, puis expire, et pouvoir me déconnecter, afin que personne ne puisse réutiliser ma session.
- **US-3** : En tant qu'utilisateur, je veux créer une organisation et en devenir Propriétaire, afin d'y faire venir mon équipe.
- **US-4** : En tant que Propriétaire ou Admin, je veux inviter des personnes dans mon organisation avec un rôle précis, afin de contrôler qui y entre et ce qu'elles peuvent faire.
- **US-5** : En tant qu'utilisateur invité dans une organisation, je veux voir l'invitation et l'accepter ou la refuser, afin de rejoindre l'organisation en connaissance de cause.
- **US-6** : En tant qu'utilisateur membre de plusieurs organisations, je veux choisir mon organisation active et passer de l'une à l'autre, afin de travailler pour plusieurs clients sans mélanger leurs données.
- **US-7** : En tant que Propriétaire ou Admin, je veux changer le rôle d'une personne ou la retirer de l'organisation, avec un effet immédiat, afin de gérer les arrivées et les départs.
- **US-8** : En tant que Propriétaire, je veux nommer ou retirer des Admins et d'autres Propriétaires, transférer la propriété et renommer l'organisation, en étant sûr qu'elle garde toujours au moins un Propriétaire, afin qu'elle ne se retrouve jamais sans responsable.
- **US-9** : En tant que Propriétaire ou Admin, je veux consulter le journal d'audit de mon organisation, afin de savoir qui a fait quoi et quand.
- **US-10** : En tant que Membre, je veux continuer à utiliser les agents existants (spec 000), en étant sûr que mes conversations ne sont lisibles par personne d'autre.
- **US-11** : En tant que client de Forge, je veux la garantie qu'aucun utilisateur d'une autre organisation, ni l'opérateur de la plateforme, ne peut voir mes données.
- **US-12** : En tant que développeur, je veux lancer l'environnement complet en local avec une seule commande, sans installer de base de données à la main.

## Exigences fonctionnelles

### A. Connexion avec Google

- **FR-1** : Le système DOIT permettre de se connecter avec n'importe quel compte Google, qu'il s'agisse d'un compte personnel (Gmail) ou d'un compte Google Workspace. Le type de compte ne DOIT pas changer le comportement.
  *Pourquoi : les clients n'ont pas tous Google Workspace, et le type de compte « n'a pas d'importance » (ADR-0002, section 1). Restreindre à Workspace exclurait des utilisateurs sans raison.*

- **FR-2** : Le système DOIT refuser la connexion d'un compte dont Google n'atteste pas que l'adresse email est vérifiée.
  *Pourquoi : les invitations reposent sur l'adresse email (FR-19). Une adresse non vérifiée permettrait à quelqu'un de se faire passer pour le destinataire d'une invitation.*

- **FR-3** : Un utilisateur DOIT être identifié par l'identifiant stable que Google attribue au compte, et non par son adresse email seule. Le nom, l'adresse email et la photo affichés DOIVENT être mis à jour à chaque connexion.
  *Pourquoi : une adresse email peut changer ou être réattribuée. L'identifiant du compte, lui, ne change jamais : c'est la seule clé fiable pour dire « c'est bien la même personne ».*

- **FR-4** : La connexion NE DOIT demander à Google que l'identité de base (identifiant, nom, adresse email, photo). Elle NE DOIT demander aucun accès à Drive ni à d'autres données Google.
  *Pourquoi : principe du moindre privilège. L'accès à Drive arrive en spec 005, avec ses propres règles (ADR-0002 D4). Demander un accès dont on n'a pas besoin inquiète l'utilisateur et augmente les dégâts en cas de fuite.*

- **FR-5** : L'échange avec Google DOIT être mené par le serveur (schéma BFF, cf. ADR-0002 D2). Le navigateur NE DOIT jamais recevoir ni stocker un jeton Google. Une réponse de connexion qui n'a pas été initiée par ce même navigateur, ou qui est rejouée, DOIT être refusée sans créer de session.
  *Pourquoi : un jeton lisible par la page peut être volé par n'importe quel script injecté (XSS). Le refus des réponses rejouées ou falsifiées empêche un attaquant de connecter la victime à son propre compte, ou de réutiliser une connexion interceptée.*

- **FR-6** : Il NE DOIT exister aucun moyen de se connecter sans passer par Google, en dehors des tests automatisés, qui simulent le fournisseur d'identité.
  *Pourquoi : un « raccourci de connexion pour le développement » finit tôt ou tard activé par erreur en production. C'est une porte dérobée.*

### B. Sessions

- **FR-7** : Après une connexion réussie, le système DOIT créer une session conservée côté serveur. Le navigateur ne reçoit qu'un cookie qui la désigne. Ce cookie DOIT être inaccessible au JavaScript de la page, transmis uniquement sur une connexion chiffrée (le poste local du développeur excepté), et non envoyé par les requêtes provenant d'autres sites, sauf la simple navigation (cf. ADR-0002 D2).
  *Pourquoi : la session côté serveur peut être révoquée à tout moment, contrairement à un jeton autonome. Les propriétés du cookie bloquent le vol par script (XSS) et réduisent les actions déclenchées depuis un autre site (CSRF).*

- **FR-8** : Une session DOIT expirer après **24 heures d'inactivité**, et au plus tard **7 jours** après sa création, quelle que soit l'activité. Ces deux durées DOIVENT être configurables. Une requête faite avec une session expirée DOIT être refusée comme non authentifiée.
  *Pourquoi : une session éternelle oubliée sur un poste partagé est une session volée en puissance. L'expiration absolue limite la durée d'utilisation d'un cookie dérobé, même s'il est utilisé en continu. 7 jours est un peu long, mais la révocation immédiate (FR-9, FR-26) compense : on peut couper un accès sans attendre l'expiration.*

- **FR-9** : La déconnexion DOIT invalider la session côté serveur immédiatement. Le même cookie, présenté de nouveau après la déconnexion, DOIT être refusé.
  *Pourquoi : se déconnecter doit vraiment couper l'accès, pas seulement effacer le cookie dans un navigateur (une copie du cookie resterait sinon utilisable).*

- **FR-10** : Chaque connexion réussie DOIT produire une nouvelle session. Un identifiant de session émis avant la connexion NE DOIT jamais devenir une session authentifiée.
  *Pourquoi : c'est la parade à la « fixation de session », où un attaquant impose à sa victime un identifiant de session qu'il connaît, puis attend qu'elle se connecte.*

- **FR-11** : Toute requête qui modifie des données DOIT porter une preuve anti-CSRF valide, liée à la session. Sans cette preuve, la requête DOIT être refusée, et rien ne DOIT être modifié.
  *Pourquoi : le navigateur joint le cookie automatiquement, y compris quand un site malveillant déclenche la requête. La preuve anti-CSRF montre que la requête vient bien de l'interface de Forge (ADR-0002 D2, conséquences).*

- **FR-12** : La vérification de la session et des permissions NE DOIT pas nécessiter d'appel à Google à chaque requête.
  *Pourquoi : la latence resterait sinon dépendante de Google, et une panne de Google bloquerait les utilisateurs déjà connectés.*

### C. Organisations et organisation active

- **FR-13** : Tout utilisateur connecté DOIT pouvoir créer une organisation, en libre-service. Une organisation DOIT avoir un nom. L'utilisateur qui crée une organisation en DEVIENT Propriétaire.
  *Pourquoi : une organisation sans responsable ne pourrait inviter personne, et le créateur est la seule personne légitime pour ce rôle au départ. Le libre-service est le plus simple pour un pilote. L'accès reste de toute façon limité : en mode test, l'application Google n'accepte que 100 utilisateurs déclarés (ADR-0002 D2).*

- **FR-13 bis** : Un Propriétaire DOIT pouvoir renommer l'organisation.
  *Pourquoi : le nom est la seule propriété d'une organisation dans cette spec. Il doit pouvoir être corrigé, et seulement par ceux qui la gouvernent.*

- **FR-14** : Un utilisateur DOIT pouvoir appartenir à plusieurs organisations, avec un seul rôle par organisation (cf. ADR-0002 D2).
  *Pourquoi : un consultant ou un prestataire travaille souvent pour plusieurs clients. Un seul rôle par organisation garde les droits lisibles.*

- **FR-15** : Chaque requête authentifiée DOIT agir dans **exactement une** organisation active, choisie par l'utilisateur parmi celles dont il est membre. Le serveur DOIT vérifier l'adhésion à chaque requête et NE DOIT jamais se fier à une organisation indiquée par le navigateur sans cette vérification. L'utilisateur DOIT pouvoir changer d'organisation active sans se reconnecter.
  *Pourquoi : si une requête pouvait toucher deux organisations à la fois, ou une organisation dont on n'est pas membre, l'isolation (FR-34) n'aurait plus de sens. Le navigateur est contrôlé par l'utilisateur : tout ce qu'il envoie peut être falsifié.*

- **FR-16** : Un utilisateur DOIT voir la liste de ses organisations, avec son rôle dans chacune. Il NE DOIT voir aucune information sur les organisations dont il n'est pas membre, pas même leur existence.
  *Pourquoi : la simple liste des clients de Forge est déjà une information confidentielle.*

- **FR-17** : Un utilisateur connecté qui n'est membre d'aucune organisation DOIT voir un écran qui le lui indique. Il y trouve ses invitations en attente et la possibilité de créer une organisation. Il NE DOIT avoir accès à aucune autre fonction.
  *Pourquoi : sans organisation active, aucune action métier n'a de sens (FR-15). Il faut quand même guider la personne au lieu de lui montrer une erreur.*

### D. Invitations

- **FR-18** : Un Propriétaire ou un Admin DOIT pouvoir inviter une personne par son adresse email, en précisant le rôle attribué. Un Admin ne peut attribuer que les rôles Membre ou Invité. Un Propriétaire peut attribuer tous les rôles, y compris Admin et Propriétaire (cf. ADR-0002 D3 : « nommer des admins » est réservé au Propriétaire).
  *Pourquoi : on n'entre dans une organisation que sur décision de quelqu'un qui en a la responsabilité. Personne ne peut créer, par une invitation, un rôle plus élevé que ceux qu'il a le droit d'attribuer (FR-30).*

- **FR-19** : Une invitation NE DOIT pouvoir être acceptée que par un utilisateur connecté dont l'adresse email vérifiée correspond à celle de l'invitation (sans tenir compte des majuscules). L'invité DOIT accepter ou refuser explicitement. Une invitation n'est utilisable qu'une seule fois.
  *Pourquoi : personne ne doit pouvoir s'approprier une invitation adressée à quelqu'un d'autre, et personne ne doit se retrouver membre d'une organisation sans l'avoir voulu.*

- **FR-20** : Une invitation en attente DOIT expirer **7 jours** après sa création (durée configurable). Un Propriétaire ou un Admin DOIT pouvoir la révoquer tant qu'elle n'est pas acceptée. Un Admin ne peut révoquer que les invitations qu'il aurait le droit de créer. Une invitation expirée ou révoquée DOIT être refusée.
  *Pourquoi : une invitation oubliée reste une porte d'entrée ouverte. On doit pouvoir la refermer, et elle doit se refermer seule.*

- **FR-21** : Inviter une personne déjà membre de l'organisation DOIT être refusé avec un message explicite. Changer son rôle passe par FR-27.
  *Pourquoi : éviter qu'une invitation serve de moyen détourné de changer un rôle, en contournant les règles de FR-27.*

- **FR-22** : L'invitation en attente DOIT apparaître à l'invité quand il se connecte avec l'adresse invitée. Forge n'envoie aucun email : c'est la personne qui invite qui prévient l'invité par ses propres moyens. L'invitation est le **seul** moyen de rejoindre une organisation. Il n'y a pas d'adhésion automatique par domaine.
  *Pourquoi : envoyer des emails ajouterait un service externe, sa configuration et ses risques (délivrabilité, usurpation), sans rien apprendre de plus sur la sécurité (constitution VII). L'adhésion par domaine n'a pas de sens pour les comptes Gmail personnels, et elle ouvrirait l'organisation à toute personne du domaine sans décision explicite.*

### E. Rôles et permissions

- **FR-23** : Le système DOIT gérer quatre rôles d'organisation : Propriétaire, Admin, Membre et Invité. Dans le périmètre de cette spec, ils donnent les droits suivants (extrait de la matrice ADR-0002 D3, limité à ce qui existe dans la 001) :

  | Action | Propriétaire | Admin | Membre | Invité |
  |---|:-:|:-:|:-:|:-:|
  | Voir ses organisations et son propre rôle, choisir l'organisation active | ✅ | ✅ | ✅ | ✅ |
  | Quitter l'organisation (FR-29) | ✅ (sauf dernier Propriétaire) | ✅ | ✅ | ✅ |
  | Utiliser les agents intégrés (spec 000) | ✅ | ✅ | ✅ | ❌ (FR-31) |
  | Voir les membres et les invitations en attente | ✅ | ✅ | ❌ | ❌ |
  | Inviter, retirer, changer de rôle des Membres et des Invités ; révoquer leurs invitations | ✅ | ✅ | ❌ | ❌ |
  | Nommer ou retirer des Admins | ✅ | ❌ | ❌ | ❌ |
  | Consulter le journal d'audit de l'organisation | ✅ | ✅ | ❌ | ❌ |
  | Nommer ou retirer des Propriétaires (FR-27 bis) | ✅ | ❌ | ❌ | ❌ |
  | Renommer l'organisation (FR-13 bis) | ✅ | ❌ | ❌ | ❌ |

  Les autres lignes de la matrice D3 dépendent de specs futures et ne sont PAS couvertes ici : choisir les modèles (002), créer et gérer des bases de connaissances (003), créer des agents (008), utiliser les agents partagés (008), fixer des quotas (008), supprimer l'organisation (reportée, voir « Hors périmètre »).
  *Pourquoi : on n'implémente que les droits qui ont un objet aujourd'hui. Une permission sans fonctionnalité derrière, c'est du code « pour plus tard » (constitution VII), et une source de confusion.*

- **FR-24** : Chaque action protégée DOIT être associée à une **permission** nommée. Les rôles sont des ensembles de permissions, définis en un seul endroit. Les contrôles d'accès DOIVENT porter sur des permissions, jamais sur des noms de rôles (cf. constitution XI, ADR-0002 D3).
  *Pourquoi : si le code teste « est-ce un admin ? » à cinquante endroits, ajouter ou modifier un rôle oblige à retrouver ces cinquante endroits, et un seul oubli crée une faille. Avec des permissions, la règle est écrite une seule fois.*

- **FR-25** : **Refus par défaut.** Toute fonction exposée par le serveur DOIT exiger une session valide et déclarer la permission qu'elle requiert. Seules la vérification de santé et les étapes de la connexion en sont exemptées, et cette liste d'exemptions DOIT être explicite. Un contrôle automatique DOIT échouer si une fonction exposée n'a ni permission déclarée ni exemption explicite.
  *Pourquoi : la faille la plus fréquente, c'est la nouvelle route qu'on a oublié de protéger. Avec le refus par défaut et le contrôle automatique, l'oubli casse les tests au lieu d'ouvrir une brèche.*

- **FR-26** : Un changement de rôle ou un retrait de l'organisation DOIT prendre effet dès la requête suivante de la personne concernée, sans qu'elle ait besoin de se reconnecter.
  *Pourquoi : quand quelqu'un quitte l'entreprise ou perd sa confiance, l'accès doit être coupé tout de suite, pas à l'expiration de sa session.*

- **FR-27** : Un Admin DOIT pouvoir retirer des Membres et des Invités, et faire passer une personne de Membre à Invité ou d'Invité à Membre. Il NE DOIT pas pouvoir modifier ni retirer un Admin ou un Propriétaire. Un Propriétaire DOIT pouvoir nommer un Admin (depuis Membre ou Invité) et retirer ce rôle (cf. ADR-0002 D3).
  *Pourquoi : la matrice D3 sépare la gestion courante des équipes (Admin) de la gouvernance de l'organisation (Propriétaire). Un Admin qui pourrait destituer les autres Admins prendrait le contrôle de l'organisation.*

- **FR-27 bis** : Une organisation PEUT avoir plusieurs Propriétaires. Un Propriétaire DOIT pouvoir nommer Propriétaire un autre membre, et retirer ce rôle à un autre Propriétaire (sous réserve de FR-28). Le transfert de propriété se fait en deux temps : nommer le nouveau Propriétaire, puis quitter le rôle ou l'organisation.
  *Pourquoi : un seul Propriétaire, c'est un point de défaillance unique. S'il part ou perd l'accès à son compte Google, l'organisation est bloquée. Le transfert en deux temps évite d'inventer une opération spéciale : il réutilise deux actions qui existent déjà, et la règle FR-28 garantit qu'il n'y a jamais de trou entre les deux.*

- **FR-28** : Une organisation DOIT toujours avoir au moins un Propriétaire. Toute action qui retirerait, rétrograderait ou ferait partir le dernier Propriétaire DOIT être refusée.
  *Pourquoi : une organisation sans Propriétaire ne pourrait plus nommer d'Admin ni être gouvernée. Et l'opérateur de la plateforme n'a pas d'accès implicite pour la débloquer (FR-36).*

- **FR-29** : Un utilisateur DOIT pouvoir quitter une organisation de lui-même, sauf s'il en est le dernier Propriétaire.
  *Pourquoi : on ne doit pas rester membre d'une organisation contre son gré.*

- **FR-30** : **Pas d'auto-élévation.** Personne NE DOIT pouvoir s'attribuer à soi-même un rôle plus élevé, ni attribuer à quelqu'un d'autre un rôle qu'il n'a pas le droit d'attribuer (FR-18, FR-27).
  *Pourquoi : c'est le principe « aucune élévation de privilèges » (constitution XI), appliqué à la gestion des rôles elle-même.*

### F. Agents existants et conversations

- **FR-31** : Les fonctions existantes de liste et d'exécution des agents (spec 000) DOIVENT exiger une session valide, une organisation active et la permission « utiliser les agents intégrés ». Les Propriétaires, Admins et Membres ont cette permission. Les Invités ne l'ont pas : selon la matrice D3, un Invité n'utilise que les agents **partagés avec lui**, et le partage n'existe qu'en spec 008. D'ici là, le refus par défaut s'applique (FR-25), et un Invité n'a accès à aucun agent.
  *Pourquoi : c'est la correction annoncée dans le document d'architecture (« Ce qui va changer dans le socle actuel »). Et donner aux Invités l'accès aux agents intégrés « en attendant » les ferait sortir de leur rôle sans décision explicite.*

- **FR-32** : Chaque conversation DOIT appartenir à un utilisateur et à une organisation. Seul cet utilisateur, et seulement quand cette organisation est active, DOIT pouvoir la poursuivre ou en lire le contenu. Une tentative avec l'identifiant d'une conversation qui appartient à un autre utilisateur ou à une autre organisation DOIT être refusée, sans révéler si cette conversation existe. Un Propriétaire ou un Admin NE DOIT pas pouvoir lire les conversations des autres.
  *Pourquoi : aujourd'hui, connaître l'identifiant d'une conversation suffit pour la poursuivre et faire ressortir son contexte. Avec plusieurs utilisateurs, ce serait une fuite. « L'admin gère, il ne lit pas » (ADR-0002 D3, règle 1) s'applique aussi aux conversations.*
  La conservation des conversations après un redémarrage du serveur n'est pas exigée par cette spec. Le choix (001 ou 003) est laissé au plan, comme le prévoit le document d'architecture.

### G. Isolation des organisations

- **FR-33** : Toute donnée métier DOIT appartenir à exactement une organisation. Seuls l'identité d'un utilisateur et ses sessions sont communes à toutes ses organisations, puisqu'un utilisateur peut appartenir à plusieurs organisations.
  *Pourquoi : l'appartenance doit être explicite sur chaque donnée pour que l'isolation puisse s'appliquer sans exception (constitution X).*

- **FR-34** : L'isolation entre organisations DOIT être garantie **au niveau de la base de données** : même une requête écrite sans aucun filtre sur l'organisation NE DOIT renvoyer, ni modifier, aucune donnée d'une autre organisation que l'organisation active. Le code DOIT en plus filtrer explicitement par organisation (cf. constitution X, ADR-0002 D1).
  *Pourquoi : un filtre dans le code dépend de l'attention du développeur à chaque requête, et il suffit d'un oubli. La garantie dans la base est une deuxième barrière indépendante : c'est la défense en profondeur. Le pire incident possible pour Forge, c'est qu'un client voie les données d'un autre.*

- **FR-35** : Chaque type de donnée métier introduit par cette spec (organisations, adhésions, invitations, conversations, journal d'audit) DOIT être couvert par un **test de fuite** automatisé. Ce test prouve qu'un utilisateur de l'organisation A ne voit, ni ne modifie, aucune donnée de l'organisation B, y compris par une requête sans filtre.
  *Pourquoi : la constitution X l'impose. Sans test, l'isolation n'est qu'une promesse, et elle peut se casser sans bruit lors d'une évolution.*

- **FR-36** : L'application NE DOIT disposer d'aucun moyen de contourner l'isolation lors de ses accès normaux aux données. Il NE DOIT exister aucun rôle ni aucune permission qui donne accès aux données de toutes les organisations. L'opérateur de la plateforme n'a aucun accès implicite aux données des organisations (cf. ADR-0002 D3, règle 3). Le mécanisme d'accès de support (demandé explicitement, limité dans le temps, journalisé) n'est pas construit dans cette spec : voir « Hors périmètre ».
  *Pourquoi : un « super-admin » est la cible idéale d'un attaquant, et il détruit la confiance des clients. Si l'application elle-même ne peut pas contourner l'isolation, une faille dans l'application ne le peut pas non plus.*

- **FR-37** : Une demande qui vise une ressource d'une autre organisation DOIT recevoir la même réponse qu'une demande qui vise une ressource inexistante.
  *Pourquoi : répondre « interdit » au lieu de « introuvable » confirmerait l'existence de la ressource, ce qui est déjà une fuite d'information.*

### H. Journal d'audit

- **FR-38** : Le système DOIT inscrire au journal d'audit de l'organisation concernée au moins les événements suivants : création et renommage de l'organisation ; invitation créée, acceptée, refusée, révoquée ou expirée à l'usage ; changement de rôle ; retrait d'un membre ; départ volontaire ; et chaque action refusée faute de permission dans l'organisation. Chaque entrée DOIT indiquer qui a agi, quelle action, sur quelle cible, quand, et avec quel résultat.
  *Pourquoi : en cas d'incident, il faut pouvoir reconstituer qui a donné quel accès à qui. Les refus répétés signalent aussi une tentative d'abus.*

- **FR-39** : Les connexions réussies, les connexions refusées, les déconnexions et les tentatives avec une session expirée ou révoquée DOIVENT être journalisées côté plateforme. Ces événements ne sont pas rattachés à une organisation, puisqu'ils précèdent le choix de l'organisation active.
  *Pourquoi : détecter les abus de connexion sans exposer à une organisation l'activité de ses membres dans les autres organisations.*

- **FR-40** : Le journal d'audit d'une organisation DOIT être consultable, en lecture seule, par ses Propriétaires et ses Admins, et par personne d'autre. Il NE DOIT être ni modifiable ni supprimable par l'application. Il est soumis à l'isolation (FR-34).
  *Pourquoi : un journal qu'on peut modifier ne prouve rien. Et le journal d'une organisation est lui-même une donnée de cette organisation.*

- **FR-41** : Aucun journal NE DOIT contenir de secret : ni jeton Google, ni identifiant de session, ni preuve anti-CSRF.
  *Pourquoi : les journaux sont lus par plus de personnes, et conservés plus longtemps, que les données qu'ils décrivent. Un secret dans un journal est un secret qui a fuité.*

### I. Interface

- **FR-42** : Un visiteur qui ouvre Forge DOIT voir une page de connexion avec un bouton « Se connecter avec Google », et rien d'autre de l'application.
- **FR-43** : Une fois connecté, l'utilisateur DOIT voir son organisation active et pouvoir en changer. S'il n'a aucune organisation, il voit l'écran de FR-17.
- **FR-44** : Les écrans de gestion (membres, invitations, journal d'audit) NE DOIVENT apparaître qu'aux utilisateurs qui ont la permission correspondante. Masquer un écran NE DOIT pas tenir lieu de protection : le serveur reste la seule autorité (FR-25).
  *Pourquoi : montrer un bouton qui mène à une erreur est frustrant. Mais l'interface est contrôlée par l'utilisateur, donc la sécurité ne peut pas reposer sur elle.*
- **FR-45** : Un Invité qui ouvre le chat DOIT voir un message clair indiquant qu'aucun agent ne lui est partagé pour l'instant, plutôt qu'une erreur technique.
- **FR-46** : Quand la session expire ou est révoquée en cours d'utilisation, l'interface DOIT ramener l'utilisateur à la page de connexion avec un message explicite.
- **FR-47** : Une action refusée DOIT afficher un message compréhensible, qui ne révèle aucune information sur d'autres organisations.

### J. Environnement de développement

- **FR-48** : Un développeur DOIT pouvoir lancer l'environnement complet (base de données, serveur, interface) avec une seule commande, sans installer de base de données à la main. La structure de la base DOIT être mise en place ou mise à jour automatiquement.
  *Pourquoi : un environnement difficile à lancer décourage les tests et crée des écarts entre les postes. La base doit être la même que celle de la cible, pour que l'isolation testée en local soit bien celle de la production.*

- **FR-49** : Les données locales DOIVENT être conservées d'un lancement à l'autre et pouvoir être réinitialisées par une commande documentée.
  *Pourquoi : ne pas perdre ses organisations de test à chaque redémarrage, tout en pouvant repartir de zéro.*

- **FR-50** : Les tests automatisés NE DOIVENT appeler ni Google ni un vrai LLM. Les tests d'isolation DOIVENT s'exécuter contre le même moteur de base de données que la cible, avec l'isolation active. La commande unique de vérification du projet DOIT les inclure.
  *Pourquoi : des tests gratuits et rapides sont des tests qu'on lance (constitution III). Un test d'isolation qui tourne sur un autre moteur que la cible ne prouverait rien sur la garantie de FR-34.*

## Comportement de l'agent

Sans objet : cette spec ne crée ni ne modifie aucun agent. Elle change seulement **qui** peut utiliser les agents existants (FR-31) et **à qui** appartiennent les conversations (FR-32). Le comportement de l'agent `assistant` de la spec 000 est inchangé pour un utilisateur autorisé.

## Critères d'acceptation

Tous les critères sont automatisables sans appel à Google (fournisseur d'identité simulé) ni au LLM (modèle factice). Aucun n'est « (live) ».

### Connexion et sessions

- **AC-1** (US-1, FR-1) : **Étant donné** un compte Google Workspace et un compte Gmail personnel, tous deux avec une adresse vérifiée, **quand** chacun termine la connexion, **alors** une session est créée pour chacun, et leur comportement est identique.
- **AC-2** (FR-2) : **Étant donné** un compte Google dont l'adresse email n'est pas vérifiée, **quand** il termine la connexion, **alors** la connexion est refusée, aucune session n'est créée et un refus est journalisé (FR-39).
- **AC-3** (FR-3) : **Étant donné** un utilisateur déjà connu, dont l'adresse email a changé chez Google mais pas l'identifiant du compte, **quand** il se reconnecte, **alors** il retrouve le même compte Forge, avec les mêmes adhésions, et l'adresse affichée est la nouvelle.
- **AC-4** (FR-4) : **Quand** la connexion est lancée, **alors** la demande envoyée à Google ne porte que sur l'identité de base, et sur aucun accès à Drive.
- **AC-5** (FR-5) : **Étant donné** une réponse de connexion qui n'a pas été initiée par ce navigateur, ou une réponse déjà utilisée une fois, **quand** elle est présentée au serveur, **alors** elle est refusée et aucune session n'est créée.
- **AC-6** (FR-5, FR-7) : **Étant donné** une connexion réussie, **quand** on examine la réponse reçue par le navigateur, **alors** elle ne contient aucun jeton Google, et le cookie de session est marqué inaccessible au JavaScript, réservé aux connexions chiffrées et non envoyé par les requêtes d'autres sites hors navigation.
- **AC-7** (FR-6) : **Étant donné** la configuration de l'application en dehors des tests, **quand** on recense les moyens de créer une session, **alors** le seul est la connexion Google.
- **AC-8** (FR-8) : **Étant donné** une session inactive depuis plus de 24 heures, **quand** une requête est faite avec cette session, **alors** elle est refusée comme non authentifiée. **Étant donné** une session inactive depuis un peu moins de 24 heures, **alors** la requête aboutit.
- **AC-9** (FR-8) : **Étant donné** une session utilisée en continu mais créée il y a plus de 7 jours, **quand** une requête est faite, **alors** elle est refusée comme non authentifiée.
- **AC-10** (US-2, FR-9) : **Étant donné** un utilisateur connecté qui a gardé une copie de son cookie, **quand** il se déconnecte puis présente la copie, **alors** la requête est refusée comme non authentifiée.
- **AC-11** (FR-10) : **Étant donné** un identifiant de session obtenu avant la connexion, **quand** l'utilisateur se connecte, **alors** cet ancien identifiant ne donne accès à rien, et la session authentifiée a un identifiant différent.
- **AC-12** (FR-11) : **Étant donné** un utilisateur connecté, **quand** une requête de modification (par exemple une invitation) arrive avec son cookie mais sans preuve anti-CSRF valide, **alors** elle est refusée et rien n'est modifié.
- **AC-13** (FR-12) : **Étant donné** un utilisateur connecté et le fournisseur d'identité simulé rendu indisponible, **quand** l'utilisateur fait une requête autorisée, **alors** elle aboutit.

### Organisations et organisation active

- **AC-14** (US-3, FR-13) : **Étant donné** un utilisateur connecté, membre d'aucune organisation, **quand** il crée l'organisation « Acme », **alors** il en est l'unique Propriétaire, et l'événement figure au journal d'audit d'Acme.
- **AC-59** (FR-13 bis) : **Étant donné** Acme, **quand** un Propriétaire la renomme « Acme SA », **alors** le nouveau nom est affiché à tous ses membres et le renommage est journalisé. **Quand** un Admin, un Membre ou un Invité tente de la renommer, **alors** la demande est refusée.
- **AC-15** (US-6, FR-14, FR-15) : **Étant donné** Alice, Membre d'Acme et Admin de Globex, **quand** elle passe d'Acme à Globex, **alors** ses requêtes suivantes s'exécutent dans Globex avec les permissions d'Admin, sans nouvelle connexion.
- **AC-16** (FR-15) : **Étant donné** Alice, membre d'Acme seulement, **quand** elle désigne Globex comme organisation active, **alors** la demande est refusée et son organisation active ne change pas.
- **AC-17** (FR-16) : **Étant donné** Alice, membre d'Acme seulement, **quand** elle demande la liste de ses organisations, **alors** elle ne voit qu'Acme, et rien sur Globex.
- **AC-18** (FR-17) : **Étant donné** un utilisateur connecté qui n'est membre d'aucune organisation, **quand** il tente d'utiliser un agent, **alors** la demande est refusée, et l'interface affiche l'écran « aucune organisation » avec ses invitations en attente.

### Invitations

- **AC-19** (US-4, FR-18) : **Étant donné** un Admin d'Acme, **quand** il invite bob@exemple.com comme Membre, **alors** l'invitation est créée en attente et journalisée.
- **AC-20** (FR-18, FR-30) : **Étant donné** un Admin d'Acme, **quand** il tente d'inviter quelqu'un comme Admin ou comme Propriétaire, **alors** la demande est refusée et le refus est journalisé.
- **AC-21** (US-5, FR-19) : **Étant donné** une invitation en attente pour bob@exemple.com, **quand** Bob se connecte avec cette adresse vérifiée (écrite Bob@Exemple.com) et accepte, **alors** il devient membre d'Acme avec le rôle prévu, et l'invitation ne peut plus être réutilisée.
- **AC-22** (FR-19) : **Étant donné** une invitation pour bob@exemple.com, **quand** un utilisateur connecté avec une autre adresse tente de l'accepter, **alors** la demande est refusée, et il ne devient pas membre.
- **AC-23** (FR-19) : **Étant donné** une invitation en attente, **quand** l'invité la refuse, **alors** il ne devient pas membre, l'invitation n'est plus utilisable et le refus est journalisé.
- **AC-24** (FR-20) : **Étant donné** une invitation créée il y a plus de 7 jours, ou révoquée, **quand** l'invité tente de l'accepter, **alors** la demande est refusée.
- **AC-25** (FR-20) : **Étant donné** une invitation au rôle d'Admin créée par un Propriétaire, **quand** un Admin tente de la révoquer, **alors** la demande est refusée.
- **AC-26** (FR-21) : **Étant donné** Bob, déjà Membre d'Acme, **quand** un Admin l'invite comme Invité, **alors** la demande est refusée avec un message explicite, et le rôle de Bob est inchangé.
- **AC-60** (FR-22) : **Étant donné** une invitation pour bob@exemple.com, **quand** elle est créée, **alors** aucun email n'est envoyé par Forge, et l'invitation apparaît à Bob dès sa connexion suivante avec cette adresse. **Étant donné** un utilisateur d'un domaine dont d'autres adresses sont membres d'Acme, mais sans invitation, **quand** il se connecte, **alors** il n'est membre d'aucune organisation.

### Rôles et permissions

- **AC-27** (FR-23) : **Pour chaque** case de la matrice FR-23, **étant donné** un utilisateur qui a ce rôle, **quand** il tente l'action, **alors** elle aboutit si la case est ✅ et elle est refusée si la case est ❌ (test paramétré qui couvre toute la matrice).
- **AC-28** (FR-24) : **Étant donné** la définition des rôles, **quand** on modifie l'ensemble des permissions d'un rôle à l'endroit unique où il est défini, **alors** le comportement de toutes les actions concernées change en conséquence, sans autre modification.
- **AC-29** (FR-25) : **Étant donné** l'ensemble des fonctions exposées par le serveur, **quand** le contrôle automatique s'exécute, **alors** il échoue si l'une d'elles n'a ni permission déclarée ni exemption explicite, et la liste des exemptions se limite à la santé et à la connexion.
- **AC-30** (FR-25) : **Étant donné** un visiteur sans session, **quand** il appelle n'importe quelle fonction non exemptée, **alors** il reçoit un refus « non authentifié ».
- **AC-31** (US-7, FR-26) : **Étant donné** Bob, Membre connecté d'Acme, **quand** un Admin le fait passer Invité, **alors** sa requête suivante d'utilisation d'un agent est refusée, sans qu'il se soit reconnecté.
- **AC-32** (US-7, FR-26) : **Étant donné** Bob, connecté avec Acme active, **quand** un Admin le retire d'Acme, **alors** sa requête suivante dans Acme est refusée, et ses adhésions aux autres organisations restent intactes.
- **AC-33** (FR-27) : **Étant donné** un Admin d'Acme, **quand** il tente de retirer ou de rétrograder un autre Admin ou un Propriétaire, **alors** la demande est refusée et journalisée.
- **AC-34** (US-8, FR-27) : **Étant donné** un Propriétaire d'Acme, **quand** il nomme Bob Admin puis lui retire ce rôle, **alors** les permissions de Bob suivent chaque changement, et les deux événements sont journalisés.
- **AC-35** (US-8, FR-28) : **Étant donné** Acme, qui n'a qu'un seul Propriétaire, **quand** quelqu'un tente de retirer ce Propriétaire, de le rétrograder, ou quand il tente lui-même de quitter Acme, **alors** chaque demande est refusée, et Acme a toujours un Propriétaire.
- **AC-61** (FR-18, FR-27 bis) : **Étant donné** Alice, Propriétaire d'Acme, **quand** elle invite Carole comme Propriétaire et que Carole accepte, **alors** Acme a deux Propriétaires, avec les mêmes permissions.
- **AC-62** (FR-27 bis, FR-28) : **Étant donné** Alice, seule Propriétaire d'Acme, et Bob, Membre, **quand** Alice nomme Bob Propriétaire puis quitte Acme, **alors** les deux actions aboutissent, Bob est l'unique Propriétaire, et les deux événements sont journalisés (transfert de propriété).
- **AC-63** (FR-27 bis) : **Étant donné** Acme avec deux Propriétaires, Alice et Bob, **quand** Alice retire à Bob son rôle de Propriétaire, **alors** la demande aboutit et Bob garde son adhésion avec le rôle attribué. **Quand** Alice, désormais seule Propriétaire, tente de se retirer elle-même ce rôle, **alors** la demande est refusée (FR-28).
- **AC-36** (FR-29) : **Étant donné** Bob, Membre d'Acme, **quand** il quitte Acme, **alors** il n'en est plus membre, sa requête suivante dans Acme est refusée et son départ est journalisé.
- **AC-37** (FR-30) : **Étant donné** un Admin ou un Membre, **quand** il tente de modifier son propre rôle vers un rôle plus élevé, **alors** la demande est refusée.

### Agents et conversations

- **AC-38** (US-10, FR-31) : **Étant donné** un Membre connecté avec une organisation active, **quand** il liste les agents puis interroge l'agent `assistant` (en réponse complète et en flux), **alors** il obtient les mêmes résultats que ceux décrits par les AC de la spec 000 (non-régression).
- **AC-39** (FR-31, FR-45) : **Étant donné** un Invité connecté, **quand** il tente de lister ou d'interroger un agent, **alors** la demande est refusée, et l'interface affiche « aucun agent ne vous est partagé pour l'instant ».
- **AC-40** (FR-32) : **Étant donné** une conversation d'Alice dans Acme, **quand** Bob, Membre ou Admin d'Acme, tente de la poursuivre avec son identifiant, **alors** la demande reçoit la même réponse que pour une conversation inexistante, et aucun élément du contexte d'Alice n'apparaît.
- **AC-41** (FR-32) : **Étant donné** une conversation d'Alice dans Acme, **quand** Alice tente de la poursuivre avec Globex comme organisation active, **alors** la demande reçoit la même réponse que pour une conversation inexistante.

### Isolation et opérateur

- **AC-42** (US-11, FR-34, FR-35) : **Pour chaque** type de donnée métier de cette spec, **étant donné** des données dans Acme et dans Globex, **quand** une lecture sans aucun filtre d'organisation est exécutée dans le contexte d'Acme, **alors** seules les données d'Acme sont renvoyées.
- **AC-43** (FR-34, FR-35) : **Pour chaque** type de donnée métier de cette spec, **quand** une modification ou une suppression sans filtre d'organisation est exécutée dans le contexte d'Acme, **alors** aucune donnée de Globex n'est modifiée ou supprimée.
- **AC-44** (FR-34) : **Étant donné** une tentative de créer, dans le contexte d'Acme, une donnée rattachée à Globex, **quand** elle est exécutée, **alors** la base la refuse.
- **AC-45** (FR-36) : **Étant donné** l'accès aux données qu'utilise l'application en fonctionnement normal, **quand** une lecture est tentée sans organisation active déclarée, **alors** aucune donnée métier n'est renvoyée.
- **AC-46** (FR-36) : **Étant donné** la définition des rôles et des permissions, **quand** on la parcourt, **alors** aucune permission ne donne accès à plusieurs organisations, ni à une organisation dont on n'est pas membre.
- **AC-47** (FR-37) : **Étant donné** une invitation ou un membre de Globex, **quand** un Admin d'Acme tente de le consulter, de le modifier ou de le révoquer par son identifiant, **alors** il reçoit la même réponse que pour un identifiant inexistant.

### Journal d'audit

- **AC-48** (US-9, FR-38, FR-40) : **Étant donné** une séquence d'actions dans Acme (invitation, acceptation, changement de rôle, retrait, refus de permission), **quand** un Admin consulte le journal d'Acme, **alors** chaque action y figure avec son auteur, sa cible, sa date et son résultat, et aucune entrée de Globex n'y apparaît.
- **AC-49** (FR-40) : **Étant donné** un Membre ou un Invité d'Acme, **quand** il tente de consulter le journal d'audit, **alors** la demande est refusée.
- **AC-50** (FR-40) : **Étant donné** l'ensemble des fonctions exposées, **quand** on les recense, **alors** aucune ne permet de modifier ou de supprimer une entrée du journal.
- **AC-51** (FR-39, FR-41) : **Étant donné** une connexion réussie, une connexion refusée et une déconnexion, **quand** on examine les journaux produits, **alors** les trois événements y figurent, et aucun ne contient de jeton, d'identifiant de session ni de preuve anti-CSRF.

### Interface

- **AC-52** (FR-42) : **Étant donné** un visiteur sans session, **quand** il ouvre l'application, **alors** il voit la page de connexion avec le bouton « Se connecter avec Google », et aucun autre écran.
- **AC-53** (FR-43) : **Étant donné** un utilisateur membre de deux organisations, **quand** il change d'organisation dans le sélecteur, **alors** le nom de l'organisation active affiché change, et les écrans reflètent son rôle dans la nouvelle organisation.
- **AC-54** (FR-44) : **Étant donné** un Membre, **quand** il ouvre l'application, **alors** les écrans de gestion des membres et du journal d'audit ne lui sont pas proposés. **Étant donné** un Admin, **alors** ils lui sont proposés.
- **AC-55** (FR-46) : **Étant donné** un utilisateur dans l'application, **quand** une requête reçoit un refus « non authentifié » (session expirée ou révoquée), **alors** l'interface affiche la page de connexion avec un message indiquant que la session a pris fin.

### Environnement de développement

- **AC-56** (US-12, FR-48) : **Étant donné** un poste avec les prérequis documentés mais sans base de données installée, **quand** le développeur lance la commande unique, **alors** la base, le serveur et l'interface démarrent, et la structure de la base est à jour.
- **AC-57** (FR-49) : **Étant donné** une organisation créée localement, **quand** l'environnement est arrêté puis relancé, **alors** l'organisation existe toujours. **Quand** la commande de réinitialisation est lancée, **alors** elle n'existe plus.
- **AC-58** (FR-50) : **Quand** la commande unique de vérification du projet s'exécute sans accès réseau à Google ni au LLM, **alors** elle exécute les tests d'isolation contre le même moteur de base que la cible, avec l'isolation active, et ils passent.

## Exigences non fonctionnelles

- **Sécurité**
  - Les décisions D1, D2 et D3 de l'ADR-0002 et les principes X et XI de la constitution s'appliquent intégralement. Le plan DOIT expliquer comment chacun est respecté.
  - Défense en profondeur : une erreur dans le contrôle des permissions (couche ②) ne DOIT jamais suffire à exposer des données d'une autre organisation, car l'isolation de la base (couche ①) reste active.
  - Les messages d'erreur ne divulguent ni l'existence de ressources d'autres organisations, ni de détail technique interne.
  - Les secrets de configuration (identifiants de l'application auprès de Google, secrets de signature) restent hors du code et des specs (constitution IX).
- **Journalisation** : voir FR-38 à FR-41. La politique de conservation des journaux est hors périmètre.
- **Données personnelles** : seules les données d'identité nécessaires sont conservées (identifiant Google, nom, adresse email, photo). *Pourquoi : principe de minimisation du RGPD.*
- **Performance** : la vérification de la session, de l'organisation active et des permissions ne DOIT pas nécessiter d'appel externe (FR-12), et NE DOIT pas retarder de façon perceptible le premier token d'une réponse d'agent par rapport à la spec 000.
- **Accessibilité** : la page de connexion, le sélecteur d'organisation et les écrans de gestion sont utilisables entièrement au clavier.
- **Non-régression** : pour un utilisateur autorisé, le chat de la spec 000 garde le même comportement, flux compris.

## Hors périmètre

- Passerelle modèles et choix des fournisseurs par organisation (spec 002).
- Bases de connaissances, leurs rôles (Propriétaire de base, Éditeur, Lecteur), le dépôt de documents et la règle « l'admin gère sans lire » appliquée aux bases (spec 003).
- Connexion à Google Drive et tout accès Drive (spec 005).
- Création d'agents par les utilisateurs, partage d'agents (dont l'accès des Invités aux agents partagés), accès partiel, quotas (spec 008).
- Autres fournisseurs d'identité (Microsoft Entra, etc.) et connexion par mot de passe.
- Fournisseur d'identité managé (Identity Platform, Auth0…) : prévu au déploiement (spec 009, cf. ADR-0002 D2).
- Gestion des appareils : liste des sessions ouvertes et révocation à distance des autres sessions.
- Politique de conservation et export du journal d'audit.
- Facturation, abonnements et limites par organisation.
- Paramètres d'organisation autres que ceux listés dans la matrice FR-23.
- Suppression de compte utilisateur à la demande (droit à l'effacement côté utilisateur).
- Suppression d'une organisation : reportée à une spec où l'organisation aura des données à effacer (bases, documents). Elle devra aussi trancher le sort du journal d'audit.
- Mécanisme d'accès de support de l'opérateur (ADR-0002 D3, règle 3) : reporté à une spec où il y aura des données client à dépanner. D'ici là, FR-36 garantit qu'il n'existe aucun accès implicite.
- Envoi d'emails par Forge (invitations, notifications) et adhésion automatique par domaine.

## Décisions prises (anciennes questions ouvertes)

Tranchées par l'utilisateur le 2026-11-24.

| # | Question | Décision | Intégrée dans |
|---|---|---|---|
| Q1 | Qui peut créer une organisation ? | Tout utilisateur connecté, en libre-service | FR-13, AC-14 |
| Q2 | Comment l'invité est-il prévenu ? Adhésion par domaine ? | L'invitation apparaît à la connexion avec l'adresse invitée. Aucun email envoyé par Forge. Pas d'adhésion par domaine | FR-22, AC-60 |
| Q3 | Quelles durées ? | Session : 24 h d'inactivité, 7 jours au maximum. Invitation : 7 jours. Toutes configurables | FR-8, FR-20, AC-8, AC-9, AC-24 |
| Q4 | Gouvernance de l'organisation | Plusieurs Propriétaires possibles ; transfert = nommer puis se retirer ; renommage par un Propriétaire ; suppression reportée | FR-13 bis, FR-18, FR-27 bis, AC-59, AC-61 à AC-63 |
| Q5 | Accès de support de l'opérateur | Non construit en 001 ; seule l'absence d'accès implicite est garantie | FR-36, Hors périmètre |

Choix confirmés en même temps :
- un Invité n'a accès à aucun agent jusqu'à la spec 008 (FR-31) ;
- la liste des membres est réservée aux Propriétaires et aux Admins (FR-23) ;
- la conservation des conversations après redémarrage est laissée au plan (FR-32) ;
- la spec reste en un seul bloc, et l'implémentation sera découpée en phases dans `tasks.md`.

Il ne reste aucun marqueur `[À CLARIFIER]`.
