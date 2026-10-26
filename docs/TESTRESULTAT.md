## 0.11.0 – blockexport, write-behind och swap-experiment

Lokalt i den disponibla experiment-VM:en (root, Ubuntu 24.04, Python 3.12.3,
nbdkit 1.36.3, libnbd 1.20.0, fusepy/libfuse 2.9.9): `run_tests.py
--require-integration` ger **177 godkända tester, inga överhoppade**, inklusive
riktig TLS/NBD på loopback, FUSE-montering genom kärnan, O_DIRECT-bakända,
write-behind-invarianter, klientomstart efter SIGKILL på beständig bakända och
labbets skyddsspärrar. [GitHub Actions run 40400729827](https://github.com/jojo-swe/memory-bridge/actions/runs/40400729827)
på commit `5304552`: **177 godkända tester, inga överhoppade**, inklusive
FUSE-monteringen på runnern.

Två befintliga tester antog en oprivilegierad körning (`RLIMIT_MEMLOCK`
binder inte en process med `CAP_IPC_LOCK`); de är rättade utan att försvaga
invarianten. Installerat wheel verifierat i ny venv utanför projektmappen:
`memory-bridge block-export`, `swap-lab --help`, `pressure-workload` och
`checkpoint` fungerar.

Swap-experimentet (30 slutförda swap-körningar, 3 felinjektioner, 0
verifieringsfel) redovisas i [SWAP-RESULTAT.md](SWAP-RESULTAT.md). Det är ett
verkligt kernel-swap i en VM, inte en simulering, men bara på loopback och med
cgroup-begränsat minne. Nytta mot lokal swap visades **inte** på lagring med
50–75 µs per 4 KiB-läsning; fallback vid fryst/död givare och tappad
anslutning verifierades under aktivt swap.

## 0.10.0 – fristående installerat CLI

PR #3 mergad. https://github.com/jojo-swe/memory-bridge/actions/runs/40195407580
rapporterar **149 godkända tester, inga överhoppade**. Paketet innehåller alla
kommandon och NBD-pluginen. Paketkopian utan repots rotskript klarar hela
TLS/NBD-labbet i CI. Lokalt:136 godkända,13 överhoppade. Wheel byggdes och
installerades i en ny venv: console entry point, jämförelse och beständig
create/verify fungerade från en annan katalog.

## 0.9.0 – verifierad fjärrintegritet

PR #2 mergad. Run https://github.com/jojo-swe/memory-bridge/actions/runs/40194256363
rapporterar **145 godkända tester, inga överhoppade**. Lokalt:133 godkända,
tolv nätverkstester överhoppade. SHA-256-kontroll täcker fulla block även
vid partiella läsningar. Regressioner verifierar korrupta svar med rätt längd,
fel utanför begärd del, batchfel, överskrivning och frigöring av metadata.
Givaren kan fortfarande läsa data. Lokal lagring är betrodd.

## 0.8.0 – beständig sidlagring och verifierad omstart

[PR #1](https://github.com/jojo-swe/memory-bridge/pull/1) mergad till main.
[Run 40189544566](https://github.com/jojo-swe/memory-bridge/actions/runs/40189544566):
**138 godkända tester, inga överhoppade**, testat PR-head
`eeae0f237d22aec5e3e93eb9ba218c5169bb37a6`, merge
`cf3acb24ff468cce21a6380206541a5afa995aa9`.

126 tester passerade lokalt; tolv nätverkstester kördes i CI. Wheelbygge och
import/läs-skriv-återöppning från det byggda paketet passerade separat.

Nytt verifierat:

- Bekräftad lokal skrivning överlever SIGKILL av klientprocessen.
- Oavslutad transaktion med utspillda sidor rullas tillbaka efter SIGKILL före commit.
- Skrivning via TLS/NBD, avstängd givare och verifiering i en ny lokal klientprocess.
- Atomisk rollback över flera block, fel filformat, storlekskontroll och stora felaktiga blobs.
- Privata filer, nekade symlänkar/hårda länkar och samtidiga öppningar.
- Låset behålls om SQLite-stängning avbryts; nästa öppning tillåts först efter lyckad städning.
- Bibliotekets versionsnummer matchar distributionsmetadata.

Processkrasch är verifierad, inte fysiskt strömavbrott. Databasens beständighet
förutsätter korrekt filsystem/disk-synkronisering. Befintlig temporär bakända
och benchmarkers beständighetsnivå är oförändrade.

## 0.7.0 – komplett loopback-labb och mindre klientmetadata

[Run 40188868175](https://github.com/jojo-swe/memory-bridge/actions/runs/40188868175):
**119 godkända tester, inga överhoppade**, commit
`0909ab4c2f5c06483acc6b494c4b7a47d3e7e05e`.
Lokalt: 108 godkända, elva integrationstester överhoppade. Wheelbygge lyckades.

Verifierat i CI:

- Hela labbet med tillfällig TLS-nyckel, givare, jämförelse och städning, startat utanför repot.
- 64 KiB-anrop mot riktig NBD-givare under 128 MiB RLIMIT_AS per klientprocess.
- Bitmap: 32 bytes för 256 block i ett 1 MiB-export, med fungerande fjärrcache.
- Sammanhängande batcher, blandade datakällor, partiella kanter och sena batchfel.
- Randomiserade läsningar/skrivningar jämförda med referens före/efter cacheförlust.
- Ctrl+C/SystemExit spärrar osäkra I/O och städar nätverksprocesser; shutdown försöker alla bakändar.
- De tidigare testerna för fryst/död givare, fel nyckel och exportstorlek.

[Benchmarkrapport](benchmark-ci-0.7.0.json): ett litet loopback-prov med varm
filcache. Fjärrläget var fortsatt långsammare. RLIMIT_AS är en adressrymdsgräns,
inte mätning av systemets RAM-brist. Varken fysisk LAN-prestanda, beständig
kraschåterställning eller system-swap har verifierats.

## 0.6.0 – jämförande benchmark

[Run 40186254875](https://github.com/jojo-swe/memory-bridge/actions/runs/40186254875):
**93 godkända tester, inga överhoppade**, commit
`ba79b83779c1e4ca3c0e67e5581b1dee1b1285f6`.
Lokalt: 84 godkända och nio transporttester överhoppade. Paketbygge lyckades.

Ett enda litet CI-prov, 1 MiB och 16 läsningar/skrivningar per läge, verifierade
att jämförelsen använder en frisk TLS/NBD-cache. Medianläsning var 0,00247 ms
lokalt och 0,18668 ms med fjärrcache. Medianskrivning var 0,00308 respektive
0,20301 ms. Klientens RSS-topp var 21128 respektive 21228 KiB; NBD-barnets
separata topp var 25660 KiB. Givarens minne och systemfilcache ingår inte.

Fjärrläget var långsammare i detta prov. Det är en funktionskontroll med varm
filcache på loopback, inte en tillförlitlig studie av SSD, LAN eller minnestryck.
[Full rårapport](benchmark-ci-0.6.0.json) innehåller seed, mätvärden och begränsningar.
Regressioner fångar tappade skrivningar, korrupta läsningar, dold lokal fallback
och kontrollerar att timeout stoppar hela experimentets processgrupp.

## 0.5.0 – verklig nätverksåterhämtning

[Run 40185556989](https://github.com/jojo-swe/memory-bridge/actions/runs/40185556989):
**80 godkända tester, inga överhoppade**, commit
`1d0b8f0bc1dc4d2ed45a62c10a5b1fcade931bb9`.

Återhämtningslagret klarade både SIGKILL och SIGSTOP av riktig nbdkit-givare
med TLS på loopback. Data kunde läsas från den lokala filkopian inom testets
3-sekundersgräns och därefter skrivas igen. Hälsosam fjärrläsning verifierades
före avbrottet för att lokal fallback inte skulle maskera transportfel.
Fel nyckel och exportstorlek avvisades. Sju transportenhetstester täcker
bland annat fragmenterade svar, timeout, protokollfel och processavslut.

Lokalt: 72 godkända, åtta NBD-tester överhoppade på grund av saknade
systemberoenden. Paketbygge av 0.5.0 lyckades. Fysiskt LAN, minnestryck,
prestandavinst och system-swap är fortfarande inte verifierade.

## 0.4.0 – CI verifierad

[Run 40184413461](https://github.com/jojo-swe/memory-bridge/actions/runs/40184413461):
**69 godkända tester, inga överhoppade**, på commit
`7a67aedf5fd718939aacd29b1cf4913901bd691f`. Båda demokommandona passerade.
NBD/TLS-testerna avser den befintliga transporten; det nya återhämtningslagret
testades med lokal fil och simulerad givare.

## 0.4.0 – lokal verifiering

69 tester: 65 godkända, 4 NBD/TLS-tester hoppas över eftersom systempaketen
saknas i utvecklingsmiljön. Återhämtningsdemon med riktig temporär fil klarar
simulerat givaravbrott och fortsatta skrivningar. Ett deterministiskt test
jämför 200 överlappande skrivningar med en referens, före och efter avbrott.
CI kör samma svit med obligatoriska NBD-beroenden och tillåter inga hoppade tester.
Detta är ingen verifiering av återhämtning över nätverk eller system-swap.

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

## Version 0.3.1 — 52 tester

[Run 40182136372](https://github.com/jojo-swe/memory-bridge/actions/runs/40182136372),
commit `bace9c922bd07de1fc2aed27b8e2579b6e69ff0b`: **52 godkända, inga överhoppade.**
Den strikta testkörningen kräver att alla tester körs. Lokalt: 48 godkända
och fyra transporttester överhoppade; strikt läge avvisade korrekt detta resultat.
Regressioner täcker återanvända slumptoken efter frigöring/karantän,
entropifel vid nyckelskapande och startkontroller före nyckelläsning.
