# Experimentplan — från RAM-lager till användbar produkt

## Frågan vi ska besvara

Kan en vanlig Linux-dator slutföra ett verkligt minneskrävande jobb billigare
eller snabbare genom att låna RAM från en andra dator än med lokal SSD-swap?
Vi räknar även givarens elförbrukning och begränsningen att båda måste vara på.

## Etapp 1: transport och integritet

Kör de fyra integrationstesterna: korrekt TLS-anslutning och dataåterläsning,
fel nyckel, okrypterad anslutning och givare som dör under en session. Alla
måste passera. Upprepa över ett riktigt LAN; dokumentera OS, CPU, RAM, nätkort,
länkhastighet, kabel/Wi-Fi och programversioner. Prototypens deadline gäller
testklienten, inte en framtida kärnanslutning för swap.

## Etapp 2: swap endast i en disponibel virtuell maskin

Bygg en separat TLS-kompatibel NBD-anslutning till VM:ens blocklager. Kontrollera
enhetens identitet, exklusivitet och timeout innan någon formatering sker.
Gör detta först när etapp 1 är grön. Ingen automatisk mkswap/swapon ingår i 0.1.

Jämför en identisk VM och identisk arbetslast med:

1. Begränsat RAM och ingen swap.
2. Samma RAM och lokal SSD-swap.
3. Samma RAM och fjärr-RAM-swap.
4. Mer lokalt RAM som referens.

Använd samma CPU, data, swapkapacitet och komprimeringsinställningar. Dokumentera
SSD och nätverk; blanda inte förändringar i minneskomprimering med transportens
effekt. Kör minst fem repetitioner efter uppvärmning och rapportera median och
spridning för jobbtid, peak-RSS, swap-I/O, CPU och nätverkstrafik. Ett lämpligt
första jobb är en reproducerbar datasetbearbetning vars arbetsmängd överskrider
VM:ens RAM. Välj slutligt jobb efter vilken utrustning som finns tillgänglig.

## Etapp 3: felhantering

I en VM utan viktigt arbete: avbryt nätverket och döda givaren under belastning.
Mät tidsgränser och felutfall. Fjärr-swap utan reservkopia kan krascha program
eller hänga systemet; ett felmeddelande i testklienten betyder inte att swap
blir säker. Bestäm sedan mellan lokal skrivgenomgående kopia och replikering.
Lokal kopia kräver fortfarande diskutrymme; replikering förbrukar mer fjärr-RAM
och gör inte samtidiga avbrott omöjliga. Ingen tyst återanslutning till tom RAM.

## Produktbeslut

Gå vidare först om minst ett relevant arbetsflöde visar tydlig nytta jämfört
med lokal SSD-swap. Om nyttan endast finns på snabbt LAN riktas produkten dit.
Om SSD vinner och ingen kapacitetsfördel återstår, ompröva lösningen.

För en publik pool behövs separat klientkryptering med autentiserade block,
skydd mot gamla återspelade block, sessionsnycklar, kvoter, flera isolerade
låntagare, donor-revokering, redundans och missbruksbegränsning. TLS ensam
löser inte dessa problem. Windows kräver en egen integrationsutredning.

En framtida demo ska visa ett riktigt jobb som annars misslyckas, samt
jämförelsen med SSD. Visa inte syntetisk I/O som om den vore extra fysisk RAM.
