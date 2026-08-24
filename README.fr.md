# Action Control

*[English](README.md) | Français*

Contrôle générique, configurable via l'interface Home Assistant, de la bonne
exécution de vos commandes (`light.turn_on`, `switch.turn_off`,
`cover.set_cover_position`, ou n'importe quel autre appel de service).

Cette intégration généralise le principe de deux automations YAML :
vérifier qu'une commande a bien été appliquée, la relancer en cas d'échec,
notifier, et éventuellement déclencher une action de secours (par exemple
activer un switch) si le problème persiste — le tout sans écrire de YAML,
et sans risque de boucle de réémission grâce à un mécanisme anti-boucle
interne basé sur le `Context` de Home Assistant.

## Installation

### Via HACS

1. HACS → Intégrations → menu (⋮) → *Dépôts personnalisés*.
2. Ajouter `https://github.com/cddu33/ha-action_control` en catégorie
   *Intégration*.
3. Installer *Action Control*, puis redémarrer Home Assistant.

### Manuelle

Copier le dossier `custom_components/action_control` dans le répertoire
`custom_components` de votre configuration Home Assistant, puis redémarrer.

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
