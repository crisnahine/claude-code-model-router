# Support inbox triage policy

Every message in the shared inbox gets one priority and one owner. P1 is the most urgent and P4 the least. Owners are the teams: `security`, `legal`, `engineering`, `billing`, `support`, `sales`, or `none` for messages nobody needs to answer.

## 1. Find the rules that match

| Rule | Message | Owner | Priority |
|---|---|---|---|
| Account security | Someone got into an account who shouldn't have, credentials or API keys were exposed, a staff member clicked a phishing link or typed a password into one, or a report of a vulnerability in our product | security | P1 |
| Phishing report | A suspicious email forwarded by someone who did not click it or enter anything | security | P3 |
| Legal demand | A subpoena, court order or request from law enforcement | legal | P1 |
| Privacy request | A request to access or delete personal data (GDPR, CCPA and similar) | legal | P2 |
| Contract question | A question about our terms or a contract clause | legal | P3 |
| Outage | The product is down or unusable for all of a customer's users | engineering | P1 |
| Broken feature | One feature fails and there is no other way to do the task | engineering | P2 |
| Minor fault | One feature fails but there is a workaround, or only one person is affected | engineering | P3 |
| Billing error | Charged twice, or charged after cancelling | billing | P2 |
| Billing request | Invoice copies, receipts, tax details, refund questions | billing | P3 |
| How-to | How to do something, or a password reset the account holder asked for | support | P3 |
| Feature request | A request for something the product doesn't do | support | P4 |
| Sales | Pricing, upgrades, more seats, demos, discounts | sales | P4 |
| Automated | Out-of-office and auto-replies, delivery failures, newsletters, spam | none | P4 |

If more than one rule matches, use the one with the highest priority. If two matching rules have the same priority, the owner is the first in this order: security, legal, engineering, billing, support, sales.

An automated message is always `none` and P4, whatever it says. Nothing below changes it.

## 2. Enterprise customers

If the sender's email domain belongs to a company with an **active** Enterprise plan in `customers.csv`, raise the priority one level (P4 to P3, P3 to P2, P2 to P1). Nothing goes above P1. A company on an Enterprise trial, or whose Enterprise plan is cancelled, does not count. Our own staff (`@northpeak.test`) never count.

## 3. Follow-ups

A follow-up is a message whose subject is `Re:` and the subject of an earlier message from the same company in this inbox. A follow-up keeps the owner of that first message and takes its priority as triaged, raised one level if the follow-up arrived more than 48 hours after the first message. Don't apply the Enterprise raise again: it is already in the first message's priority.

If what the follow-up says matches a rule whose priority is higher than that, triage it by that rule instead, as a new message.
