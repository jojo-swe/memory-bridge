# Arkitektur och kontrakt — 0.3

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

Transporten är konfigurerad för TLS-PSK, men nätverkstesterna återstår i denna
miljö. Givaren är betrodd och kan läsa data. Inga token eller minnesinnehåll
ingår i kärnans aggregerade statistik. Nyckelfiler får inte versionshanteras.
Detta är inte en genomförd säkerhetsrevision eller ett skydd mot fientliga
givare. Nyckelrotation under drift, publik registrering och rate limiting finns
inte. Alla processanvändare med tillgång till PSK kan använda exporten.

Ingen automatisk leasing/TTL finns: det vore farligt att återta aktivt
swapminne utan dränering eller en aktuell reservkopia. Inga löften ges om
databevarande efter processkrasch, strömavbrott eller frånkoppling. Ingen
automatisk retry sker efter ett osäkert skrivfel. För en användarprodukt krävs
en separat, testad strategi för redundans eller lokal skrivgenomgående kopia.

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
