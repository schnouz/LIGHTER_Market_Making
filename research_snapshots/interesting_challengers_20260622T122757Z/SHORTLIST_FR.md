# Shortlist challengers MM - snapshot 2026-06-22

Ce snapshot conserve les configs et métriques utiles avant extinction possible du VPS backtest.

## Fichiers conservés

- `configs_snapshot/`: configs live et configs de tournament au moment du snapshot.
- `decision_fast_gate.json`: décision complète machine-readable.
- `decision_fast_gate.txt`: rapport lisible.
- `selected_interesting_candidates.json`: 100 candidats sélectionnés avec `full_params`.
- `selected_state_files/`: les state files exacts des 100 candidats sélectionnés.
- `latest_tournament_report.*`: rapport global du dernier tournament.

## Candidats canary les plus exploitables

| Symbole | Statut | Fills | PnL/j estimé | ROI/j bps | Adverse 5s | Paramètres |
|---|---:|---:|---:|---:|---:|---|
| TAO | canary_candidate | 15 | $7.79 | 77.9 | 7.55 bps | `sm=2.7 minh=4.0 maxh=35.0 cap=0.12 lvl=2` |
| EDGE | canary_candidate | 42 | $9.24 | 92.4 | 7.35 bps | `sm=2.7 minh=6.0 maxh=25.0 cap=0.10 lvl=2` |

Ces deux candidats passent les gates rapides `24h+ / 15 fills / PnL positif / adverse <= 8 bps`.

## Candidats à surveiller, pas prêts canary

| Symbole | Raison |
|---|---|
| HYPE | PnL/j élevé dans le paper, mais seulement 5 fills et adverse 5s autour de 18 bps. A revalider avec plus de fills. |
| ETH | Profil propre mais seulement 2 fills. Pas assez de données. |
| SUI | PnL/j fort avec 3 fills; à surveiller, mais échantillon trop faible. |
| FARTCOIN | Très peu de fills, mais markout propre sur ce snapshot. |
| JTO | 7 fills, PnL fort, mais adverse trop élevé. |
| XMR | Plusieurs variantes ressortent, mais certaines ont peu de fills ou adverse élevé; à rechecker avant canary. |

## Lecture prudente

Ces résultats viennent de challengers paper/live-paper courts. Ils sont utiles pour ne pas perdre les paramètres intéressants, mais ils ne remplacent pas une validation plus longue ni un replay L2 propre.

Pour réutiliser une config, partir du state file exact dans `selected_state_files/`, récupérer `params`, puis générer une config live avec taille/capital conservateurs.
