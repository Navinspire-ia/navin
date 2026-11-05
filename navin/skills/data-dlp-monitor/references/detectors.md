# Detectors: regex plus validation per data type

A regex alone produces 30 to 60 % false positives on numeric identifiers;
every detector below pairs a pattern with a structural or checksum check.
`scripts/classify_scan.py` implements them in this order, and a span matched
by an earlier detector is never re-used by a later one (an IBAN is not also a
phone number, a SIRET is not also a payment card).

| Type | Regex (Python) | Validation | Tier | Legal basis |
|---|---|---|---|---|
| Private key PEM | `-----BEGIN (?:RSA \|EC \|DSA \|OPENSSH \|ENCRYPTED \|PGP )?PRIVATE KEY(?: BLOCK)?-----` | none needed | Restricted | secret |
| AWS access key id | `\b(?:AKIA\|ASIA)[A-Z0-9]{16}\b` | `AKIA` long-term, `ASIA` temporary (STS); the account id is recoverable from the key | Restricted | secret |
| AWS secret key | `aws(?:_\|\s)?secret(?:_access)?(?:_\|\s)?key\W{0,5}([A-Za-z0-9/+=]{40})` (case-insensitive) | 40 chars base64, only flagged next to its label | Restricted | secret |
| JWT | `\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b` | base64url-decode the header, require `"alg"`; check `exp` claim to report expired tokens separately | Restricted | secret |
| Vendor tokens | `ghp_[A-Za-z0-9]{36}`, `github_pat_[A-Za-z0-9_]{60,}`, `xox[baprs]-...`, `sk_live_[A-Za-z0-9]{24,}`, `AIza[0-9A-Za-z_-]{35}`, `glpat-[A-Za-z0-9_-]{20}` | prefix is the validation; gitleaks `rules` carry the full list | Restricted | secret |
| IBAN | `\b[A-Z]{2}\d{2}(?:[ -]?[A-Z0-9]{4}){2,7}(?:[ -]?[A-Z0-9]{1,4})?\b` | ISO 13616 mod 97: move the first 4 chars to the end, letters to 10..35, integer mod 97 must equal 1; length per country (FR 27, DE 22, ES 24, IT 27, BE 16, GB 22) | Confidential | RGPD art. 4, DSP2 |
| French NIR (securite sociale) | `\b[12378]\s?\d{2}\s?(?:0[1-9]\|1[0-2]\|[2-9]\d)\s?(?:\d{2}\|2[AB])\s?\d{3}\s?\d{3}\s?\d{2}\b` | 13-digit body: sex (1, 2; 3, 7, 8 for temporary numbers), year, month (01-12; 20-42 and 50-99 for unknown month), department (2A -> 19, 2B -> 18 before the arithmetic), commune, order; key = 97 - (body mod 97) | Restricted | RGPD art. 87, decret 2019-341 (NIR usage) |
| SIREN | `\b\d{3}\s?\d{3}\s?\d{3}\b` | 9 digits, Luhn | Internal | public register (INSEE Sirene), business data |
| SIRET | `\b\d{3}\s?\d{3}\s?\d{3}\s?\d{5}\b` | 14 digits, Luhn on the 14 and Luhn on the first 9; exception La Poste (SIREN 356000000): digit sum multiple of 5 | Internal | public register |
| Payment card PAN | `\b(?:\d[ -]?){12,18}\d\b` | 13 to 19 digits, Luhn, issuer prefix (4 Visa, 51-55 and 2221-2720 Mastercard, 34/37 Amex, 6011/65 Discover, 35 JCB, 30/36/38 Diners, 62 UnionPay), reject repeated digits; test numbers (4111 1111 1111 1111) are still flagged | Restricted | PCI DSS req. 3, RGPD art. 4 |
| E-mail address | `\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b` | TLD alphabetic; role addresses (`noreply@`, `contact@`, `support@`) can be downgraded by an allowlist | Confidential | RGPD art. 4 |
| French phone | `(?<![\w+])(?:\+33\s?[1-9]\|0[1-9])(?:[\s.-]?\d{2}){4}(?![\w])` | 10 digits, first 0 then 1-9 (01-05 landline by zone, 06-07 mobile, 08 special, 09 VoIP); international `+33` drops the leading 0 | Confidential | RGPD art. 4 |
| Date of birth | `(?:n[ée]e? le\|date de naissance\|DOB\|born on)\W{0,3}\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}` | only with its label, otherwise any date matches | Confidential | RGPD art. 4 |
| RGPD art. 9 vocabulary | word list: health (`VIH`, `cancer`, `diabete`, `arret maladie`, `dossier medical`, `psychiatri*`, `handicap*`), trade union (`syndicat*`, `syndique*`), religion (`religion`, `musulman*`, `chretien*`, `juif*`), sexual orientation, ethnic origin, biometric (`biometrique`, `empreinte digitale`); art. 10: `casier judiciaire`, `condamnation` | context word within the same record as an identifier (name, e-mail, NIR); vocabulary alone in a policy document is not special-category data | Restricted when attached to a person | RGPD art. 9 and 10, CNIL |
| French postal address | `\b\d{1,4}(?:bis\|ter)?\s+(?:rue\|avenue\|av\.\|boulevard\|bd\|chemin\|impasse\|allee\|place)\s+[^,\n]{3,60},?\s+\d{5}\b` | 5-digit postcode 01000-98999 | Confidential | RGPD art. 4 |
| French passport / CNI | passport `\b\d{2}[A-Z]{2}\d{5}\b`; new CNI (2021) MRZ line 1 `IDFRA` + 25 chars | MRZ check digits (ICAO 9303, weights 7-3-1) | Restricted | RGPD art. 4, identity documents |
| Vehicle plate (SIV) | `\b[A-HJ-NP-TV-Z]{2}-\d{3}-[A-HJ-NP-TV-Z]{2}\b` | letters exclude I, O, U; SS forbidden | Confidential | RGPD art. 4 (indirectly identifying) |
| Fiscal number (SPI) | `\b[0-3]\d{12}\b` with label `numero fiscal` | 13 digits starting 0-3 | Restricted | RGPD art. 4, secret fiscal |

## Checksums in code

```python
def luhn_ok(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch)
        if i % 2 == 1:
            n = n * 2 - 9 if n * 2 > 9 else n * 2
        total += n
    return total % 10 == 0

def iban_ok(value: str) -> bool:
    v = value.replace(" ", "").upper()
    rearranged = v[4:] + v[:4]
    numeric = "".join(str(ord(c) - 55) if c.isalpha() else c for c in rearranged)
    return int(numeric) % 97 == 1

def nir_key(body13: str) -> int:
    n = int(body13.replace("2A", "19").replace("2B", "18"))
    return 97 - n % 97
```

## Mapping to vendor detectors

| Type | Microsoft Purview SIT | Google Cloud DLP infoType | AWS Macie managed identifier | gitleaks rule id |
|---|---|---|---|---|
| IBAN | `International Banking Account Number (IBAN)` | `IBAN_CODE` | `BANK_ACCOUNT_NUMBER` (US), none for IBAN: custom identifier | - |
| French NIR | `France Social Security Number (INSEE)` | `FRANCE_NIR` | `FRANCE_NATIONAL_IDENTIFICATION_NUMBER` | - |
| SIREN / SIRET | custom SIT (regex + Luhn via `IsValidLuhn` not available: use keyword `SIRET`) | custom infoType | custom identifier | - |
| Payment card | `Credit Card Number` | `CREDIT_CARD_NUMBER` | `CREDIT_CARD_NUMBER` | - |
| E-mail | `Email Address` (not a built-in SIT in Purview: use `Email` entity in custom SIT) | `EMAIL_ADDRESS` | `EMAIL_ADDRESS` | - |
| French phone | `France Phone Number` | `PHONE_NUMBER` (location FR) | `PHONE_NUMBER` | - |
| Passport FR | `France Passport Number` | `FRANCE_PASSPORT` | `FRANCE_PASSPORT_NUMBER` | - |
| CNI FR | `France National ID Card (CNI)` | `FRANCE_CNI` | `FRANCE_NATIONAL_IDENTIFICATION_NUMBER` | - |
| Fiscal SPI | `France Tax Identification Number` | `FRANCE_TAX_IDENTIFICATION_NUMBER` | `FRANCE_TAX_IDENTIFICATION_NUMBER` | - |
| AWS key | `Amazon S3 Client Secret Access Key` (credential SIT) | `AWS_CREDENTIALS` | `AWS_CREDENTIALS` | `aws-access-token` |
| JWT | `Azure AD Client Secret` family does not cover JWT: custom SIT | `JSON_WEB_TOKEN` | `JSON_WEB_TOKEN` | `jwt` |
| Private key | `General Password` family: use `Azure Storage Account Key` or custom | `ENCRYPTION_KEY` | `PRIVATE_KEY` | `private-key` |
| GitHub token | `GitHub Personal Access Token` | `GITHUB_TOKEN`... not built-in: `AUTH_TOKEN` | `GITHUB_CREDENTIALS` | `github-pat` |
| Health terms | `All Medical Terms And Conditions` (EDM/keyword dictionary) | `MEDICAL_TERM` | custom | - |

## Tuning false positives

- Require a keyword within 300 characters for numeric types in prose (Purview `supporting elements`, Cloud DLP `hotwordRule`): `IBAN`, `RIB`, `NIR`, `securite sociale`, `carte`, `CB`.
- Exclude test and documentation values: `4111 1111 1111 1111`, `FR76 3000 6000 0112 3456 7890 189`, `AKIAIOSFODNN7EXAMPLE`, `wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY` are all public samples; still report them in code repositories because they indicate a secret-handling pattern.
- Count distinct values, not matches: a CSV with one e-mail repeated 10 000 times is one data subject.
- A file with more than 50 distinct NIR, IBAN or PAN is a bulk dataset: treat it as Restricted regardless of the individual types and name a data owner.
