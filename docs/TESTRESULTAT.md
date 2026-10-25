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

Ej verifierat: två fysiska datorer, swap-integration,
SSD-jämförelse, produktionssäkerhet och prestandavinst. NBD-paketen saknas här.
Fyra integrationstester kördes senare framgångsrikt i GitHub Actions (se nedan). NAS:en behövs inte för kärntester eller lokal demo.

Ingen disk, swapkonfiguration eller brandvägg har ändrats av prototypen.

Regressioner i 0.2.1: skrivskydd i adaptern, immutabel kvotbokföring,
entropifel och tokenkollision före allokering, samt avvisning av symlänkar,
FIFO-filer och för stora nyckelfiler. Alla sju nya tester passerar.

## GitHub Actions — verifierad körning

[Run 40181213266](https://github.com/jojo-swe/memory-bridge/actions/runs/40181213266)
på commit `d8647192d7fbf7306cfa48cb32fdde044433c89d`: **40 av 40 tester godkända, inga överhoppade.**

Ubuntu 24.04, nbdkit 1.36.3, libnbd 1.20.0. Inkluderar riktig TLS-PSK/NBD
på loopback, dataåterläsning, nekad återanslutning, fel nyckel, nekad
okrypterad anslutning och givarprocess som dödas under sessionen.

## Version 0.3.0 — 47 tester

[Run 40181712375](https://github.com/jojo-swe/memory-bridge/actions/runs/40181712375)
på commit `fa43b875c3e3f50a58a1bdb15c982a982f40f6fb`: **47 godkända tester, inga överhoppade.**
Lokalt passerade 43 tester; fyra transporttester kördes i CI. Nya tester täcker
TOML-kvoter i adaptern, annonserad I/O-gräns, ogiltig NBD-blockstorlek,
CLI-konfiguration och dränering med bevarade sessioner.
