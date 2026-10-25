# Verifiering 2026-10-25 — ramverk 0.2.1

Miljö: Ubuntu 24.04.3, Python 3.12.14, memlock-gräns 8192 KiB.

Kommando: `python3 -m unittest discover -s tests -v`

Resultat: **36 godkända tester, 4 överhoppade transporttester (40 totalt).**

Verifierat: isolerade allokeringar, ägarkvot, totalbudget, sessionsgräns,
40 samtidiga allokeringsförsök mot fyra tillgängliga platser, 500 randomiserade
skriv/läsjämförelser, gränskontroll, konfiguration, korta läsningar, injicerade
läs-/skrivfel, fel vid frigöring, karantän och avveckling trots enskilda fel.
Även verkligt låst RAM, fatal låsningsbrist, nyckelfilsrättigheter och
NBD-pluginens callbacks testades. Callbacktest är inte transporttest.

Kommandona `python3 -m memory_bridge demo`, `doctor` och `validate-config
config/local.toml` kördes framgångsrikt. Demon använder simulerade minneslager
och visar isolation, spärrad trasig session och fortsatt funktion för en
opåverkad låntagare. Den mäter inte nätverksprestanda eller minnesbesparing.

Ej verifierat: verklig NBD/TLS-anslutning, två fysiska datorer, swap-integration,
SSD-jämförelse, produktionssäkerhet och prestandavinst. NBD-paketen saknas här.
Fyra integrationstester och GitHub Actions-workflow medföljer; workflow har
inte körts på GitHub. NAS:en behövs inte för kärntester eller lokal demo.

Ingen disk, swapkonfiguration eller brandvägg har ändrats av prototypen.

Regressioner i 0.2.1: skrivskydd i adaptern, immutabel kvotbokföring,
entropifel och tokenkollision före allokering, samt avvisning av symlänkar,
FIFO-filer och för stora nyckelfiler. Alla sju nya tester passerar.
