<p align="center">
  <img src="https://raw.githubusercontent.com/cddu33/ha-action_control/main/custom_components/action_control/brand/logo.png" alt="Action Control" width="256">
</p>

# Action Control

*[English](https://github.com/cddu33/ha-action_control/blob/main/README.md) | Français*

Contrôle générique, configurable via l'interface Home Assistant, de la bonne
exécution de vos commandes (`light.turn_on`, `switch.turn_off`,
`cover.set_cover_position`, ou n'importe quel autre appel de service).

Elle vérifie qu'une commande a bien été appliquée, la relance en cas
d'échec, notifie, et peut déclencher une action de secours (par exemple
activer un switch) si le problème persiste — le tout sans écrire de YAML,
et sans risque de boucle de réémission grâce à un mécanisme anti-boucle
interne basé sur le `Context` de Home Assistant.

## Fonctionnalités

- **Vérification générique des commandes** — surveille n'importe quel appel
  de domaine/service, pas seulement light/switch/cover.
- **Ciblage personnalisable par règle** — domaine(s), service(s), motif glob
  d'`entity_id`, motif de nom, pièces, étiquettes et/ou appareils.
- **Vérification avec tolérance** — tolérance scalaire (ex. `brightness`
  ±5), tolérance élément par élément pour les attributs de type liste
  (`rgb_color`, `xy_color`), égalité stricte pour le texte et les booléens.
- **Détection de mouvement** — pour les volets par exemple, attend qu'un
  attribut (ex. `current_position`) commence réellement à changer plutôt que
  de comparer un instantané.
- **Sortie immédiate si déjà satisfait** — une commande sans effet, ou déjà
  appliquée quand l'événement se déclenche, se résout instantanément, sans
  délai ni notification.
- **Relances configurables** — nombre et délai, par règle.
- **Escalade configurable** — action de secours optionnelle (activer un
  switch, lancer un script...) déclenchée après échec persistant, avec un
  délai de recharge entre deux escalades et un délai avant de rejouer la
  commande d'origine.
- **Notifications** — notification persistante et/ou service `notify.*` de
  votre choix, par règle.
- **Protection anti-boucle intégrée** — chaque commande réémise porte son
  propre `Context` mémorisé, donc l'événement `call_service` correspondant
  est reconnu et ignoré avant de pouvoir redéclencher une règle. Aucune
  entité de garde à configurer.
- **Entièrement configurable par l'interface** — Config Flow (installation)
  + Options Flow (ajout/modification/suppression de règles, paramètres
  globaux). Aucun YAML nécessaire.
- **Capteur de statut par règle** — un capteur de diagnostic (`ok` /
  `retrying` / `escalated` / `failed`) avec le détail de la dernière
  vérification.
- **Règles suspendables** — une règle peut être désactivée sans être
  supprimée.
- **Interface bilingue** — français et anglais.

## Installation

### Via HACS

1. HACS → Intégrations → menu (⋮) → *Dépôts personnalisés*.
2. Ajouter `https://github.com/cddu33/ha-action_control` en catégorie
   *Intégration*.
3. Installer *Action Control*, puis redémarrer Home Assistant.

### Manuelle

Copier le dossier `custom_components/action_control` dans le répertoire
`custom_components` de votre configuration Home Assistant, puis redémarrer.

### À propos de l'icône

L'intégration embarque sa propre icône sous `custom_components/action_control/brand/`.
Home Assistant 2026.3.0+ la sert automatiquement via l'API proxy locale
[Brands Proxy API](https://developers.home-assistant.io/blog/2026/02/24/brands-proxy-api)
(aucune configuration nécessaire). C'est aussi la version minimale exigée
par HACS pour cette intégration (`hacs.json`).

## Configuration

Paramètres → Appareils et services → Ajouter une intégration → *Action
Control*. Toute la configuration (règles de surveillance, options
d'escalade, notifications) se fait ensuite depuis le bouton *Configurer*
de l'intégration — aucun YAML n'est nécessaire.

Chaque règle définit :

- **Ciblage** : domaine(s), service(s), motif d'`entity_id`, motif de nom,
  pièces, étiquettes, appareils.
- **Vérification** : délai avant contrôle, attributs à vérifier avec
  tolérance (ex. `brightness`, `rgb_color`), nombre de relances et délai
  entre relances. Les domaines `light`, `switch` et `cover` sont préremplis
  avec des valeurs par défaut adaptées.
- **Escalade** (optionnelle) : une action de secours (ex. activer un
  switch, relancer un script) déclenchée si la vérification échoue de
  manière persistante, avec un délai minimum entre deux escalades.
- **Notifications** : notification persistante et/ou service `notify.*` de
  votre choix.

Voir la [documentation complète](https://github.com/cddu33/ha-action_control/blob/main/docs/documentation.fr.md) pour la
référence détaillée de chaque champ, des exemples prêts à l'emploi, la
journalisation de débogage et les limites connues.

## Exemple d'usage

- Une règle sur le domaine `light` vérifie que la luminosité et la couleur
  demandées ont bien été appliquées, avec tolérance, et relance la commande
  jusqu'à 2 fois en cas d'échec.
- Une règle sur le domaine `cover`, avec un motif `cover.volet_*`, attend
  qu'un volet commence réellement à bouger ; si ce n'est pas le cas, elle
  active un switch de redémarrage de passerelle puis rejoue la commande.

## Dépannage

Activer les journaux de débogage :

```yaml
logger:
  logs:
    custom_components.action_control: debug
```

Voir la [documentation](https://github.com/cddu33/ha-action_control/blob/main/docs/documentation.fr.md) pour plus de détails,
notamment la marche à suivre quand une règle ne se déclenche pas.
