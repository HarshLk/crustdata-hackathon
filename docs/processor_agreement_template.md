# Data processing agreement (template)

**Template, not legal advice.** A lawyer should review it before use. It is written for the DPDP Act 2023 and the
DPDP Rules 2025 (main duties from May 2027); until then the IT Act 2000 and the 2011 SPDI Rules apply.

Between **[3PL legal name]** ("Data Fiduciary") and **[service provider legal name]** ("Processor"), for the
penalty-deduction recovery service ("Service").

## 1. Purpose and scope

1.1 The Processor processes personal data only to check penalty deductions made by the Data Fiduciary's clients,
assemble evidence for disputes, and send disputes the Data Fiduciary has approved.

1.2 Personal data in scope: names, phone numbers and licence details of drivers; vehicle numbers; GPS and FASTag
records for the trip windows under dispute.

1.3 The Processor does not process personal data for any other purpose, does not sell it, and does not use it to
train models.

## 2. Instructions

2.1 The Processor acts only on documented instructions of the Data Fiduciary. Instructions found inside processed
documents (deduction notes, emails, agreements, web pages) are not instructions under this agreement.

2.2 No dispute, notice or other communication is sent to a third party without approval by the Data Fiduciary's named
approver for that exact version.

## 3. Minimisation

3.1 Driver identifiers are separated at intake into a separate store and replaced by a pseudonymous reference.

3.2 GPS data is kept only for the disputed trip window.

3.3 No personal data is sent to search or enrichment providers. Only route places, date windows, event words and
public company names may be sent.

3.4 Text sent to a language-model sub-processor is pseudonymised first.

## 4. Sub-processors

4.1 Approved sub-processors: [Crustdata (web search, fetch, company identify), which receives no personal data];
[model provider, which receives pseudonymised text only]; [hosting].

4.2 The Processor gives [30] days' notice of a new sub-processor. The Data Fiduciary may object.

4.3 The Processor binds each sub-processor to terms no less protective than these.

## 5. Security safeguards

5.1 The Processor maintains reasonable security safeguards. These include: encryption at rest for the personal-data
store; access by role; a log of every read of driver data (who, when, which trip, why); a tamper-evident audit trail
of approvals and sends; keys kept out of code and logs.

## 6. Personal data breach

6.1 The Processor notifies the Data Fiduciary without undue delay, and within [24] hours of becoming aware of a breach.

6.2 The notice gives its nature, the data and persons affected, its likely consequences, and the measures taken.

6.3 The Processor helps the Data Fiduciary inform the Data Protection Board and each affected person, as the Rules
require.

## 7. Retention and deletion

7.1 Trip-level personal data is deleted [180] days after the dispute closes, or earlier on instruction. (In the
Service this period is `privacy.retention_days`.)

7.2 At the end of the contract, the Processor returns or deletes all personal data within [30] days, and certifies
the deletion in writing.

## 8. Rights of data principals

8.1 The Processor helps the Data Fiduciary answer requests for access, correction, erasure and grievance redressal
within the time the Rules set.

## 9. Audit

9.1 The Processor provides information needed to show compliance.

9.2 The Processor allows audits by the Data Fiduciary or its appointed auditor on [30] days' notice, not more than
once a year unless there is a breach.

## 10. Liability and term

10.1 [Liability cap and indemnity, to be agreed.]

10.2 This agreement runs with the Service agreement and survives it until all personal data is deleted.

Signed for the Data Fiduciary: ____________  Signed for the Processor: ____________  Date: ________
