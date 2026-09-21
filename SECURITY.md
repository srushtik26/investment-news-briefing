# Security Policy

Security reports and responsible vulnerability disclosures are welcome. The maintainer appreciates the efforts of security researchers and developers in identifying potential risks and helping ensure the safety and reliability of this project.

---

## Scope of Vulnerabilities

Relevant security considerations and vulnerabilities for this project include:

* **Credential & Secret Exposure**: Exposed API keys, secrets, email credentials, or accidental credential logging in console output or CI logs.
* **External URL & Ingestion Safety**: Unsafe handling of external URLs, Server-Side Request Forgery (SSRF)-style issues, or redirects to malicious destinations.
* **Content Injection & Prompt Escapes**: Indirect prompt injection, markdown injection, or sanitization failures originating from untrusted third-party article HTML or RSS payloads.
* **CI/CD Pipeline Security**: Misconfigured GitHub Actions workflows, unauthorized workflow runs, secret exfiltration vectors, or dependency supply-chain risks.
* **Dependency Vulnerabilities**: Known Common Vulnerabilities and Exposures (CVEs) in pinned third-party dependencies.
* **Dashboard & Storage Exposure**: Unauthorized access, data leakage, or injection vectors affecting SQLite or dashboard interfaces.

---

## Reporting a Vulnerability

If you discover a security vulnerability or potential security risk:

1. **Do NOT open a public GitHub Issue**: Please refrain from publicly publishing vulnerability details, proof-of-concept exploits, or sensitive technical reproduction steps before the maintainer has had an opportunity to investigate and address the issue.
2. **Use GitHub Private Vulnerability Reporting**: Submit your report directly and confidentially via the repository's **Security** tab:
   - Navigate to the repository on GitHub.
   - Click the **Security** tab.
   - Select **Advisories** and click **Report a vulnerability**.

### What to Include in Your Report

To help resolve reports efficiently, please include:
* A description of the vulnerability and its potential impact.
* Step-by-step reproduction instructions or a minimal proof of concept.
* Any relevant components, scripts, or workflow files involved.
* Proposed remediations or patches, if available.

---

## Policy & Expectations

* **No Formal Warranties**: This open-source pipeline is provided on an "as-is" basis as outlined in the [MIT License](LICENSE). The project does not claim third-party security certifications or formal independent security audits.
* **Commitment to Resolution**: The maintainer investigates verified security findings promptly and will publish fixes or security advisories as appropriate once resolved.
