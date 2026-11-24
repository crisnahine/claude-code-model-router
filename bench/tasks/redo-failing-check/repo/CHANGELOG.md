# Changelog

All notable changes to the shop are listed here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the shop uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [2.3.1] - 2026-10-29

### Fixed

- Orders paid with a gift card no longer show a zero total in the confirmation email. (#209)
- The address form keeps the country you picked when a postcode fails to validate. (#211)

## [2.3.0] - 2026-10-14

### Added

- Customers can download an invoice PDF from their order page. (#196)
- Norwegian krone in the currency picker. (#201)

### Changed

- Product search ranks exact title matches first. (#199)

### Deprecated

- The `/v1/checkout` endpoint. Use `/v2/checkout` instead; `/v1` goes away in a later release. (#203)

### Fixed

- The stock count on the product page updates after a refund. (#198)

[Unreleased]: https://git.example.com/shop/compare/v2.3.1...HEAD
[2.3.1]: https://git.example.com/shop/compare/v2.3.0...v2.3.1
[2.3.0]: https://git.example.com/shop/compare/v2.2.4...v2.3.0
