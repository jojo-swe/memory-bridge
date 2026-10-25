# Arkitektur och kontrakt — 0.8

## Moduler

| Fil | Ansvar |
| --- | --- |
| memory_bridge/core.py | Kvoter, sessioner, I/O-gränser, statistik, avveckling |
| memory_bridge/storage.py | Linux mmap + obligatorisk mlock, rensning och frigöring |
| memory_bridge/simulation.py | Lokal testbackend och injicerade fel |
| memory_bridge/config.py | Strikt TOML till validerade Limits, även för donor-startaren |
| memory_bridge/__main__.py | Demo, miljödiagnostik, konfigurationsvalidering |
| ram_plugin.py | nbdkit-adapter över kärnan, en session per process |
| bridge.py | Nyckelgenerering och start med obligatorisk TLS-PSK |
| benchmark.py | Syntetiskt NBD-test i subprocess med total tidsgräns |

## Ägande och tillstånd

MemoryPool äger lagret. allocate kontrollerar kvoter och skapar lagret innan
sessionen registreras. Slumpmässiga kapabilitetstoken lämnas till anroparen.
Token ska hållas hemliga; API:t är internt och innebär ingen autentisering av
ägarnamnet. Ett framtida kontroll-API måste härleda ägare från verifierad
identitet. Att låta nätverksklienter välja eget ägarnamn kringgår ägarkvoten.

En session går från aktiv till frigjord eller felspärrad. Det finns ingen
återaktivering. Ett nytt token innebär en ny, nollställd allokering. Poolens
stängning är slutgiltig. Allokering och I/O serialiseras med ett lås; ett
framtida nätverksbackend måste därför ha egna deadlines och bör få en annan
schemaläggare. Dagens backend är lokalt RAM, inte rekursiv fjärrlagring.

Läs- och skrivfel spärrar hela den drabbade sessionen. Det undviker att ett
delvis genomfört skriv återförs som framgång. Kort läsning avvisas. Felaktiga
anroparargument avvisas utan att förstöra en frisk session. Om frigöring
misslyckas hålls minnet kvar i kvoträkningen som karantän. Shutdown försöker
städa alla sessioner även om en misslyckas. Karantän kräver processomstart.

## Resursgränser

Totalbudget och ägarkvot avser databytes. Sessionsgräns begränsar metadata.
I/O-storlek begränsar kopior per anrop. Dessa är inte en absolut gräns för
processens RSS: Python, TLS, nbdkit och buffertar kräver mer. Kärnan förutsätter
betrodda backend-implementationer som respekterar Storage-kontraktet och städar
efter misslyckad konstruktion. Nätverksadaptern använder alltid LockedMemory;
simulerat minne kan inte väljas genom donor-startarens flaggor.

## Säkerhet och tillgänglighet

Transporten kräver TLS-PSK; verkliga nätverkstester körs på loopback i CI. Givaren är betrodd och kan läsa data. Inga token eller minnesinnehåll
ingår i kärnans aggregerade statistik. Nyckelfiler får inte versionshanteras.
Detta är inte en genomförd säkerhetsrevision eller ett skydd mot fientliga
givare. Nyckelrotation under drift, publik registrering och rate limiting finns
inte. Alla processanvändare med tillgång till PSK kan använda exporten.

Ingen automatisk leasing/TTL finns: det vore farligt att återta aktivt
swapminne utan dränering eller en aktuell reservkopia. Inga löften ges om
databevarande efter processkrasch, strömavbrott eller frånkoppling. Ingen
automatisk retry sker efter ett osäkert skrivfel. För en användarprodukt krävs
ytterligare VM/LAN-verifiering av lokal skrivgenomgående kopia och avbrott.

## Testnivåer

1. Kärntester: kvoter, samtidighet, isolation, randomiserad I/O och injicerade fel.
2. Backendtester: verkligt låst RAM och fatal låsningsbrist på Linux.
3. Adaptertester: plugin-callbacks mot samma kärna, utan NBD på nätverket.
4. Transporttester: verklig nbdkit + libnbd, TLS, fel nyckel och avbruten givare.
5. Framtida VM/LAN-test: swap, minnestryck och jämförelse med SSD.

Nivåerna är separata. En grön lokal demo bevisar inte fungerande TLS eller
prestandaförbättring. GitHub-workflow kräver NBD-beroenden innan testen startar;
projektmappens innehåll ska vara repots rot om workflow-filen används.

## Dränering i 0.3

`MemoryPool.drain()` stoppar nya allokeringar atomärt. Befintliga sessioner
kan fortsätta läsa, skriva och frigöras. Läget kan inte återställas; en stängd
pool avvisar drain. Operatören måste observera `snapshot()` innan close.
Detta är ännu inte ett fjärr-API eller automatisk datamigrering.

## Token och startkontroller i 0.3.1

Token består av generationsnummer och en 256-bitars slumpkomponent.
Generationsnumret förhindrar återanvändning inom samma pool även vid
upprepad slumpdata, utan att spara avslutade token. Mellan pooler förlitar
sig unikheten på slumpgeneratorn. Token får inte parsas av anroparen.
Startkontroll av memlock anger endast om begäran ryms inom OS-gränsen;
den garanterar inte ledigt RAM. Själva mlock måste fortfarande lyckas.

## Låntagarlager i 0.5

`local_disk.py` äger en temporär lokal fil. `resilient.py` skriver lokalt först
och speglar hela berörda block till en valfri cache. Bara bekräftat speglade
block får läsas från cachen. Felaktig lokal I/O spärrar lagret. Fel i cache-I/O
kopplar bort cachen permanent; den lokala kopian förblir auktoritativ.

`remote_nbd.py` äger en worker-process och en privat socketpair.
`nbd_worker.py` utför blockerande libnbd-anrop med obligatorisk TLS-PSK.
Ett storleksbegränsat binärt protokoll överför kommandon och data. Varje
transportoperation delar en deadline för sändning, ramhuvud och samtliga
svarsfragment. Timeout eller protokollfel stänger socketen, dödar processen
och väntar högst 0,25 sekunder på avslut. Misslyckad städning rapporteras.

Det finns inga automatiska återförsök av osäkra nätverksskrivningar. Lokal
skrivning har redan lyckats innan en sådan skickas, så cachefel behöver inte
förlora den lokala uppdateringen. Temporär standardbakända saknar fsync och
kraschåterställning; beständig bakända finns från 0.8. Integritetskontroll mot
illvillig givare ingår inte. Föräldraprocessens plötsliga
krasch kan lämna en worker kvar om den sitter fast inne i ett nätverksanrop.

`recovery_client.py` kopplar ihop delarna för ett syntetiskt experiment.
Linux/POSIX krävs. API-användare måste validera nyckelfilen före konstruktion
och skydda sökvägen under anslutning; CLI använder den befintliga kontrollen.

## Jämförelse i 0.6

`workload.py` genererar en sida i taget med SHAKE-256 från seed och sidindex,
initierar exporten och mäter identiska slumpsekvenser. Skrivfasen genererar
nya data per operation och verifierar dem separat. Ingen skuggkopia av hela
exporten hålls i RAM; latenslistorna är begränsade till 100000 poster per fas.

`compare_benchmark.py` kör varje läge i egen processgrupp och sammanställer
JSON. Frisk fjärrcache krävs för ett giltigt fjärrresultat. En tidsgräns för
hela körningen stoppar också eventuella fastnade NBD-barnprocesser. RSS-toppar
mäts separat efter städning. Ingen slutsats om verklig RAM-besparing kan dras
utan även mätning av givare, systemfilcache och en representativ arbetslast.

## Bitmap, överföringar och labblivscykel i 0.7

Cachegiltigheten har en bit per block och en räknare. Bitmapen frigörs när
cachen stängs av; inget lika stort tillfälligt nollningsobjekt skapas. En
valfri `transfer_bytes` slår ihop intilliggande block med samma datakälla.
Partiella kantblock läses från lokal kopia; fullt täckta skrivblock kan
speglas direkt från redan lokalt skrivna anropsdata. Cachebitar sätts först
efter lyckad fjärrskrivning. Senare batchfel överger hela cachen.

`memory_budget.py` sänker RLIMIT_AS enbart i en disponibel benchmarkprocess.
Gränsen ärvs av NBD-barnprocessen och befintliga strängare gränser höjs aldrig.
Virtuell adressrymd per process är inte fysisk RAM, cgroup-gräns eller
systemfilcache. Dessa mätningar bevisar därför inte nytta under systemtryck.

`lab.py` skapar privata kortlivade autentiseringsuppgifter och en loopback-givare,
kör jämförelsen och städar före lyckad rapport. Benchmarkens processgrupp
dödas vid timeout eller avbrott. NBD-biblioteket städar även vid Ctrl+C under
anslutning eller I/O; delvis skrivna lokala data spärras vid avbrott.

## Beständig sidlagring i 0.8

`persistent.py` implementerar Storage via SQLite. Saknade sidor är nollor;
logisk storlek och formatversion sparas separat. En hel skrivbegäran är en
transaktion. Rollback sker även vid cancellation. DELETE-journal, EXTRA-sync,
avstängd mmap och begränsad sidcache kontrolleras efter konfigurering.

En separat låst descriptor hindrar samtidiga samarbetsvilliga öppnare.
SQLite öppnar ändå filer via sökväg: katalog och sökvägsstabilitet är en
förtroendegräns. Om SQLite-close misslyckas behålls ägarlåset tills städningen
kan slutföras. Databasen och eventuell journal raderas aldrig automatiskt.
Efter omstart betraktas all fjärrcache som ogiltig.

Backendens transaktionsgarantier bygger på SQLite och lagringens
synkroniseringsgarantier. Tester simulerar processkrasch, inte diskhaveri.
Se https://www.sqlite.org/atomiccommit.html och
https://www.sqlite.org/pragma.html#pragma_synchronous.

## Integritetskontroll i 0.9

Varje bekräftat cacheblock har en SHA-256-digest i låntagarens minne.
Fjärrläsningar hämtar fullständiga block och verifierar dem innan data lämnas
till anroparen. Vid avvikelse används lokal kopia och all fjärrgiltighet
avvecklas. Digestlagret är begränsat till 32 byte per block. TLS och
integritetskontrollen döljer inte klartexten från givaren. Lokal lagring
och låntagarprocessen är fortsatt betrodda.
