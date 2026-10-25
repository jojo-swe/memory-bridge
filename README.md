# Memory Bridge — ramverk 0.10.0

## Hela labbet med ett kommando

På en Linux-testdator med systempaketen installerade:

```bash
/usr/bin/python3 lab.py --operations 256 --request-kib 64 --memory-limit-mib 128
```

Kommandot skapar en tillfällig nyckel, startar en givare på loopback, jämför
lokal fil med TLS/NBD och städar givaren, klienterna och nyckeln. Ingen NAS,
swapaktivering eller brandväggsändring behövs. Om beroenden saknas:

```bash
sudo apt-get install nbdkit nbdkit-plugin-python python3-libnbd
```

Själva labbet körs som vanlig användare. Börja med standardstorleken 1 MiB.
Läs [körguiden](docs/KORGUIDE.md) för test på två datorer och resultatens gränser.

## Utveckla utan NAS

Kör följande från projektmappen med Python 3.11 eller senare. Den lokala demon
har inga externa Python-beroenden och öppnar inga nätverksportar.

```bash
# Två isolerade låntagare, simulerat givaravbrott och spärrad gammal session.
python3 -m memory_bridge demo

# Lokal filkopia överlever ett simulerat givaravbrott.
python3 -m memory_bridge recovery-demo

# Läs diagnostik utan att ändra systeminställningar.
python3 -m memory_bridge doctor

# Kontrollera kärnans exempelkonfiguration; okända inställningar avvisas.
python3 -m memory_bridge validate-config config/local.toml

# Alla tester. Transporttester hoppas över om NBD-paketen saknas.
python3 -m unittest discover -s tests -v
```

Kärnan stöder flera isolerade allokeringar med totalbudget, ägarkvot och
sessionsgräns. NBD-adaptern använder samma kärna men erbjuder fortfarande
**en enda låntagare per serverstart**. TOML-filen används även av givaren med `--config config/local.toml`.
Utan explicit `--size-mib` väljs högst 4 MiB inom ägarkvoten.
Kärnans `drain()` stoppar nya allokeringar men bevarar aktiva sessioner;
det är ännu inget fjärrkommando.

Paketet kan även installeras med `python3 -m pip install .`; då finns kommandot
`memory-bridge` för demo, doctor och validate-config. NBD-skripten körs från
projektmappen enligt instruktionerna nedan och installeras inte som CLI.

Läs [arkitekturen](docs/ARKITEKTUR.md) för tillstånd, säkerhetsgränser och vad
som återstår. Alla 149 tester passerar i [GitHub Actions](https://github.com/jojo-swe/memory-bridge/actions/runs/40195407580), inklusive NBD/TLS på loopback.

Arbetsnamn, inte kontrollerat varumärke. En första byggsten för att låna RAM
från en annan Linux-dator på ett betrott hemnätverk.

**Detta är ett experimentellt RAM-baserat NBD-lager och en testklient. Det ökar
inte automatiskt datorns RAM och aktiverar inte swap.** NBD/TLS på loopback är verifierat i CI; test på två fysiska datorer och
swap-integration återstår.

## Vad som finns

- Fast RAM-budget som låses med `mlock`; start misslyckas om den inte kan låsas.
- NBD via nbdkit, obligatorisk TLS och 256-bitars delad anslutningsnyckel.
- En autentiserad anslutning per serverstart. En andra anslutning nekas även
  efter att den första kopplats ned. Starta om givaren inför varje experiment.
- Syntetiskt skriv/lästest med slumpdata, datakontroll och medianlatens.
- Enhetstester och separata integrationstester för TLS och bortkoppling.

Givaren kan läsa innehållet: TLS skyddar överföringen, inte mot givardatorns
administratör. Ingen replikering, återanslutning, automatisk
minnesåterlämning, GUI, Windows-integration eller publik minnespool finns ännu.
Givarens RAM-data försvinner när givaren stängs eller sessionen avslutas.
Låntagaren kan välja temporär eller beständig lokal kopia enligt avsnitten nedan. Använd syntetiska
testdata och anslut inte lagret som swap på din vardagsdator.

## Börja på en Linux-testdator

Exempel för Ubuntu 24.04. Kör kommandona i projektmappen.

```bash
# Installerar systempaket. Själva prototypen körs som vanlig användare.
sudo apt-get update
sudo apt-get install nbdkit nbdkit-plugin-python python3-libnbd

# Kör tester. Använd systemets Python för att hitta libnbd-bindningen.
/usr/bin/python3 -m unittest discover -s tests -v

# Skapa nyckeln en gång; befintlig fil skrivs aldrig över.
/usr/bin/python3 bridge.py keygen session.psk

# Kontrollera hur mycket RAM processen får låsa (värdet är i KiB).
ulimit -l

# Terminal 1: 4 MiB är ett litet funktionstest, inte en prestandademo.
/usr/bin/python3 bridge.py donor --key session.psk --size-mib 4
```

I en andra terminal, i samma projektmapp:

```bash
# Skriver över testexporten och verifierar återlästa data.
/usr/bin/python3 benchmark.py --key session.psk --pages 256 --timeout 30
```

Om låsningen misslyckas: minska teststorleken till 1 MiB eller konfigurera
användarens memlock-gräns för ett senare, större experiment. Kör inte hela
programmet som root för att kringgå problemet. RAM-budgeten gäller datalagret;
Python, nbdkit, TLS och testklienten behöver ytterligare minne.

## Testa två datorer

Installera beroendena och kopiera projektet till båda datorerna. Överför samma
`session.psk` genom en betrodd kanal, exempelvis SSH, och sätt `chmod 600
session.psk` på mottagaren. Lägg inte nyckeln i Git eller meddelanden.

På givaren, ersätt exempeladressen med dess faktiska LAN-adress:

```bash
/usr/bin/python3 bridge.py donor --key session.psk --size-mib 4 --bind 192.168.1.50
```

På låntagaren:

```bash
/usr/bin/python3 benchmark.py --host 192.168.1.50 --key session.psk --pages 256 --timeout 30
```

Om en brandvägg används behöver låntagaren nå givarens TCP-port 10809. Begränsa
åtkomsten till låntagarens LAN-adress; publicera inte porten mot internet.
Starta om givaren före varje ny testklient. Ingen enhet under `/dev` ändras.

## Hur resultat ska tolkas

`median_read_ms` och `median_write_ms` avser seriella 4 KiB-operationer, inklusive
protokoll och Python-overhead. De är inte spelprestanda, total RAM-hastighet
eller ett bevis på att lösningen slår SSD. Klienten behåller testdata i sitt
eget RAM för verifiering; benchmarken demonstrerar därför inte RAM-besparing.

Nästa steg finns i [experimentplanen](docs/EXPERIMENTPLAN.md).
Verifieringsstatus finns i [testrapporten](docs/TESTRESULTAT.md).

## Tekniska källor

- https://libguestfs.org/nbdkit-python-plugin.3.html
- https://www.libguestfs.org/nbdkit-tls.1.html
- https://www.libguestfs.org/nbd_set_tls.3.html
- https://www.libguestfs.org/nbd_set_tls_username.3.html
- https://libguestfs.org/libnbd.3.html
- https://infiniswap.github.io/

Den här prototypen använder standardverktyg och ett nytt Python-lager. Den
implementerar inte Infiniswap och påstår ingen prestandaparitet med RDMA.

## Strikt verifiering och startkontroller

`python3 run_tests.py` fungerar även om skriptet anropas från en annan mapp.
CI kör `python3 run_tests.py --require-integration` och misslyckas om något
test hoppas över. Givaren kontrollerar NBD-blockstorlek och aktuell
memlock-gräns innan nyckelfilen läses; mlock vid allokering är fortsatt
den slutliga kontrollen. Sessions-token är opaka och får inte tolkas av klienter.

## Lokal återhämtning i 0.4.0

`ResilientStorage` skriver till en lokal filkopia innan givarens cache uppdateras.
Endast fullständigt speglade block får läsas från givaren. Vid ett rapporterat
givarfel kopplas cachen bort permanent och läsningar fortsätter från filen.
Fel i den lokala kopian stoppar lagret; osäkra skrivningar får inte döljas.

Kör `python3 -m memory_bridge recovery-demo --directory /sökväg/till/testdisk`.
Demon använder riktig fil-I/O och en simulerad givare. Filerna är privata och
tillfälliga; diskutrymme reserveras med `posix_fallocate` där det finns.
Välj en disk, inte tmpfs, vid minnesmätning. OS:ets filcache kan fortfarande
använda RAM. Filen raderas vid stängning, vilket inte är säker radering.

Från 0.5.0 kan bibliotekslagret anslutas till NBD med klienten nedan. Med temporär standardbakända skyddar det inte
mot processkrasch, strömavbrott eller en givare som returnerar felaktigt
innehåll med rätt längd. Generiska bakändar måste själva begränsa väntetiden; NBD-klienten nedan gör det.
Ingen återanslutning eller automatisk återuppbyggnad av cache sker. En lokal
filkopia av hela lagret behövs; någon prestandavinst är ännu inte uppmätt.

## Nätverksåterhämtning i 0.5.0

Starta en **ny** givare med 1 MiB enligt tidigare instruktioner. Kör sedan:

```bash
/usr/bin/python3 recovery_client.py --key session.psk --size-mib 1 --timeout 2 --pause-for-fault
```

Klienten skriver syntetiska data och verifierar dem över TLS/NBD. Vid pausen
stänger du givaren i dess terminal och trycker sedan Enter hos klienten.
`fallback_observed: true` visar att läsningen gick vidare med den lokala filkopian.
Utan pausen körs ett vanligt test; det påstår inte att något avbrott inträffat.
För två datorer, lägg till `--host GIVARENS_LAN_IP`. Exportstorleken måste matcha
exakt. `--directory` väljer var den tillfälliga filkopian skapas.

NBD-anropen körs i en separat process. Anslutning och varje transportoperation
har en egen tidsgräns, inklusive överföring till/från processen. Vid fel stoppas
processen och cachen överges utan återanslutning. En större klientbegäran kan
omfatta flera block och därmed flera tidsgränser. Lokal disk-I/O och väntan på
andra anrop omfattas inte av nätverkstidsgränsen; detta är inte ett realtidssystem.

Startfel avslutar experimentet, även om lokal disk är tillgänglig. Givaren kan
ha förbrukat sin enda session redan under anslutningen: starta om den efter
misslyckade försök. Nyckelfilen och dess överordnade katalog måste vara betrodda;
libnbd öppnar filen igen efter CLI-kontrollen. Full lokal kopia krävs fortfarande,
och filer överlever inte klientens avslut. Ingen system-swap aktiveras.

## Jämföra lokal fil med delat RAM – 0.6.0

Utan givare kan du mäta en lokal baslinje:

```bash
python3 compare_benchmark.py --mode local --size-mib 1 --operations 256
```

För jämförelse, starta en **ny 1 MiB-givare**, och kör:

```bash
/usr/bin/python3 compare_benchmark.py --key session.psk --size-mib 1 --operations 256
```

Båda lägena kör samma seed och sidsekvens i separata processer. Hela exporten
initieras först, utan att en extra kopia av datamängden behålls i klientens RAM.
Varje läsning verifieras; skrivningar ändrar data och verifieras med en extra,
otimad läsning. Rapporten visar median/p95, IOPS, initieringstid och separata
RSS-toppar för klienten och dess barnprocess. Minnestalen inkluderar inte
givaren eller systemets filcache och får inte summeras som samtidiga toppar.

`duration_ratios` över 1 betyder att fjärrläget var **långsammare**. Tiderna
avser endast lagringsanrop; verifiering och datagenerering ingår inte i IOPS.
Detta är buffrad fil-I/O med varm filcache, ingen mätning av kall SSD eller
verklig RAM-brist. Ingen global filcache rensas och inget minnestryck skapas.
Resultatet visar inte automatiskt RAM-besparing eller snabbare program.

Välj fysisk disk med `--directory`, anpassa `--host` för LAN och använd samma
`--size-mib` som givaren. Gör upprepade körningar med `--order remote-first`
och `--order local-first` (ny givare varje gång) för att upptäcka ordningseffekter.
`--run-timeout` begränsar hela varje delkörning; vid timeout dödas dess
processgrupp, inklusive NBD-processen. `--io-timeout` gäller varje NBD-anrop.
Om fjärrlagret faller tillbaka till fil avvisas jämförelsen som ogiltig.

Första CI-provet (1 MiB, 16 operationer per fas, loopback och varm cache) gav
långsammare fjärrläge. Detta lilla funktionstest visar ingen prestandavinst.
Rådata och exakta förutsättningar finns i
[benchmark-ci-0.6.0.json](docs/benchmark-ci-0.6.0.json).

## Mindre metadata och större anrop i 0.7.0

Cachegiltighet använder nu en bitmap: en bit per 4 KiB-block, vilket motsvarar
32 KiB för 1 GiB lagringsutrymme (utöver objekt- och processoverhead).
`valid_bitmap_bytes` i rapporten visar bitmapens faktiska storlek.

`--request-kib 64` samlar sammanhängande block i större överföringar. Värdet
måste vara en tvåpotens mellan 4 och 1024 och används lika för båda lägena.
`--memory-limit-mib 128` begränsar varje klientprocess med RLIMIT_AS. Det gäller
virtuell adressrymd per process, inte sammanlagd RAM-användning, givaren eller
systemfilcache. Alltför låg gräns avbryter experimentet. Detta ersätter inte
ett test av hela systemets minnestryck i en VM eller delegerad cgroup.

Version 0.7.0 har även verifierats med 64 KiB-anrop och en adressrymdsgräns
på 128 MiB per klientprocess. [Rårapport](docs/benchmark-ci-0.7.0.json).
Fjärrläget var fortfarande långsammare i detta lilla loopback-prov.

## Beständig lokal kopia i 0.8.0

Det nya valfria `PersistentStorage` sparar sidor i en privat SQLite-databas.
Varje skrivning är en transaktion, även över flera block. Bekräftelse sker efter
lokal commit; SQLite använder rollback-journal och `synchronous=EXTRA`.
Det befintliga labbet använder fortfarande temporära filer för sina benchmarks.

Testa beständighet med syntetiska data utan givare:

```bash
python3 checkpoint_demo.py create checkpoint.db
python3 checkpoint_demo.py verify checkpoint.db
```

`create` vägrar skriva över en befintlig fil. `verify` öppnar den efter omstart
och behöver ingen givare. För nätverkstest, starta en ny 1 MiB-givare och lägg
till `--key session.psk` (och `--host` för LAN) vid `create`. Stäng sedan givaren
och kör `verify`. Den lokala filen **behålls** efter avslut och är inte krypterad.

Samtidiga öppningar nekas med ett exklusivt fil-lås. Sökvägen och dess katalog
måste vara betrodda och stabila. Flytta inte databasen eller dess journal under
drift; kopiera inte en aktiv databas som backup. En misslyckad initiering kan
lämna en privat fil som behöver granskas innan ett nytt försök.

Vi testar SIGKILL efter bekräftad skrivning och före commit med utspillda
databassidor. Strömavbrott, trasig hårdvara och filsystem som inte respekterar
synkronisering är inte verifierade. En skrivning som saknar bekräftelse kan
efter omstart vara helt genomförd eller helt återställd. Ingen automatisk
upprepning görs. Disken behöver plats för data, databasoverhead och journal;
hela den logiska kapaciteten förallokeras inte. Detta är fortfarande inte swap.

## Verifierat innehåll från givaren i 0.9

Låntagaren sparar SHA-256 för varje block som speglas. Hela blocket verifieras
före läsning, även om bara några byte efterfrågas. Felaktigt innehåll gör att
hela fjärrcachen överges och den lokala kopian används. Metadata kostar
32 byte per block, cirka 8 MiB per GiB export, utöver bitmap och processer.
Digestdata hålls lokalt och cachen börjar ogiltig efter varje omstart.

Detta upptäcker felaktiga givarsvar utan att förhindra att givaren läser
innehållet. Kryptering mot givarens administratör ingår fortfarande inte.

## Installerade kommandon i 0.10

Alla labbkommandon ingår nu i Python-paketet och kan köras utan projektmappen:

```bash
python3 -m venv --system-site-packages .venv
.venv/bin/python -m pip install .
.venv/bin/memory-bridge lab --operations 256
.venv/bin/memory-bridge checkpoint create checkpoint.db
.venv/bin/memory-bridge checkpoint verify checkpoint.db
```

På Ubuntu behövs `python3-venv` för miljön. `--system-site-packages` gör
Ubuntu-paketets libnbd-bindning tillgänglig; NBD-systempaketen krävs fortfarande.
Övriga kommandon är `compare`, `benchmark`, `recover`, `donor` och `keygen`.
Använd `memory-bridge KOMMANDO --help`. Befintliga skript i repots rot fungerar
som kompatibilitetsingångar. Paketet installerar ingen tjänst automatiskt.
