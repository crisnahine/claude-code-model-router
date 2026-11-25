# Changelog

All notable changes to the shop are listed here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the shop uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [2.4.0] - 2026-11-20

### Added

- Export the order list as CSV from the admin. (#214)
- Swedish krona in the currency picker. (#217)

### Changed

- The order history page loads faster. (#218)

### Removed

- **Breaking:** The `/v1/checkout` endpoint, deprecated in 2.3.0, is gone. Use `/v2/checkout`. (#224)

### Fixed

- The cart keeps its total when a coupon expires during checkout. (#215)
- Canadian addresses show the right tax line. (#222)
- The API returns 404 instead of 500 for non-existent orders. (#228)

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

[Unreleased]: https://git.example.com/shop/compare/v2.4.0...HEAD
[2.4.0]: https://git.example.com/shop/compare/v2.3.1...v2.4.0
[2.3.1]: https://git.example.com/shop/compare/v2.3.0...v2.3.1
[2.3.0]: https://git.example.com/shop/compare/v2.2.4...v2.3.0
