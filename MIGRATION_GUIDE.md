# Migration Guide - Feature Smart Vinted Scoring

Cette branche `feature/smart-vinted-scoring` réintroduit le scraping Vinted de manière optimisée ("Sniper Logic") pour réduire les coûts et la charge.

## Changements Principaux

1. **Sniper Scoring (`app/jobs_scoring.py`)** :
   - Le système ne scrape plus Vinted pour *tous* les articles.
   - Il calcule d'abord un score théorique basé sur la marque et la réduction.
   - Si score > 65 (ou > 50 pour marques Hype), ALORS il lance le scrape Vinted.
   - Sinon, il se base uniquement sur le score théorique.

2. **Vinted Service Réactivé (`app/services/vinted_service.py`)** :
   - Abandonn de la classe "Disabled".
   - Utilisation de `Playwright` (via Browser Worker) pour simuler un vrai navigateur.
   - Parsing du HTML de recherche Vinted pour extraire les prix réels.

3. **Infrastruture** :
   - Ajout de `playwright` et `beautifulsoup4` dans `requirements.txt`.

## Déploiement

1. **Installer les dépendances système Playwright** :
   Sur Railway, assurez-vous que le Dockerfile installe les dépendances nécessaires pour Playwright (navigateurs Chromium).
   Si vous utilisez le buildpack standard Python, ajoutez cette commande de build ou `post_install` :
   ```bash
   playwright install chromium
   playwright install-deps
   ```
   
   *Si votre Dockerfile est custom, ajoutez :*
   ```dockerfile
   RUN pip install playwright
   RUN playwright install chromium
   RUN playwright install-deps
   ```

2. **Configuration des Proxies** :
   Assurez-vous d'avoir des proxies configurés en base de données avec le type `web_unlocker` (même si ce sont des proxies gratuits pour l'instant).
   Le service Vinted va essayer de les utiliser via `proxy_service.get_web_unlocker_proxy()`.

3. **Migration Future vers Bright Data** :
   Quand vous passerez à Bright Data :
   - Ajoutez simplement les credentials du proxy Bright Data dans la table `proxy_settings` avec `type='web_unlocker'`.
   - Le code utilisera automatiquement ce nouveau proxy puissant sans changement de code.

## Vérification
Pour tester que tout fonctionne :
1. Lancez un scrape d'un produit (ex: une paire de Nike).
2. Vérifiez les logs :
   - `Pre-score for deal...`
   - `Sniper triggered...` (si le score est bon)
   - `Vinted stats: Median=...`
3. Vérifiez que le deal a bien des `vinted_stats` en base.
