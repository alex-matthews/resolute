# Changelog

## 0.1.0 (2026-07-14)


### ⚠ BREAKING CHANGES

* **ops:** the image no longer creates a user or /data, ships no /config/policy.yaml, and defaults to nobody:nogroup — deployments must supply runAsUser/fsGroup and mount the policy ConfigMap (the shipped helmrelease already does both).

### Features

* adopt home-operations port convention (main 8080, metrics 8081) ([#27](https://github.com/alex-matthews/resolute/issues/27)) ([6c67115](https://github.com/alex-matthews/resolute/commit/6c67115c8aecf623e86c4036ffc6dd81eb6977ce))
* ADR-0002 Costanza seam — objective-worth endpoint + downgrade executor ([#36](https://github.com/alex-matthews/resolute/issues/36)) ([e72368f](https://github.com/alex-matthews/resolute/commit/e72368f5066c68445f7c04431cbd3c66e6e7cf58))
* **cli:** remote mode for review-pending ([60d603b](https://github.com/alex-matthews/resolute/commit/60d603b4f3167cf118535677107deb028280352e))


### Bug Fixes

* **api:** constant-time tokens, per-family metrics exposition, webhook body guard ([#35](https://github.com/alex-matthews/resolute/issues/35)) ([d1c9d21](https://github.com/alex-matthews/resolute/commit/d1c9d214e45f3bb81b2c8d9de9c09b5d61654bab))
* **container:** update uv (0.11.24 → 0.11.26) ([#19](https://github.com/alex-matthews/resolute/issues/19)) ([a95da3e](https://github.com/alex-matthews/resolute/commit/a95da3eb25affcc7d2f5d445f6360fa5d0f4faaa))
* **container:** update uv (0.11.26 → 0.11.28) ([#25](https://github.com/alex-matthews/resolute/issues/25)) ([b789861](https://github.com/alex-matthews/resolute/commit/b789861bdb7d31f3616aa11ef93724094c01c630))
* **deploy:** finish 8080/8081 port convention; scoped cronjob secret; doc sync ([#37](https://github.com/alex-matthews/resolute/issues/37)) ([e1d276f](https://github.com/alex-matthews/resolute/commit/e1d276f699a8d2224b12be8df39ed5c2f0b68b07))
* **docker:** image default port 8130 -&gt; 8080 ([#28](https://github.com/alex-matthews/resolute/issues/28)) ([4904431](https://github.com/alex-matthews/resolute/commit/49044313056c54891efa42b9194b7b8ea5e9d0d6))
* **engine:** word-boundary vocab and pin matching ([#34](https://github.com/alex-matthews/resolute/issues/34)) ([916934f](https://github.com/alex-matthews/resolute/commit/916934f5c12eb3fe91ce7d91582137b0e4689917))
* **ops:** identity-agnostic alpine image, fail-fast policy, k8s smoke ([b166dc8](https://github.com/alex-matthews/resolute/commit/b166dc8cc1c09fa74dc38b946273f4cfafc4c852))
* **ops:** stable /config and /data mount targets in image ([e5686bd](https://github.com/alex-matthews/resolute/commit/e5686bd99233d66c9e614dcc295dc09812b1070c))


### Documentation

* **adr:** ADR-0002 — downgrade executor + objective-worth endpoint ([#26](https://github.com/alex-matthews/resolute/issues/26)) ([260995f](https://github.com/alex-matthews/resolute/commit/260995ff4a8cef64be8558599cefd662010ac8eb))
* mise ci task describes what it actually runs ([4fe10c3](https://github.com/alex-matthews/resolute/commit/4fe10c32f4b3eeb2336a6b64b7805b8f9539767f))
* reflect 8080/8081 port split + service :80 in-cluster ([#30](https://github.com/alex-matthews/resolute/issues/30)) ([6517d76](https://github.com/alex-matthews/resolute/commit/6517d760edb6134e238ec7c0b6e5c1262ccca70c))
