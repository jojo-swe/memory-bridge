# Memory Bridge — ramverk 0.3.0

## Utveckla utan NAS

Kör följande från projektmappen med Python 3.11 eller senare. Den lokala demon
har inga externa Python-beroenden och öppnar inga nätverksportar.

```bash
# Två isolerade låntagare, simulerat givaravbrott och spärrad gammal session.
python3 -m memory_bridge demo

# Läs diagnostik utan att ändra systeminställningar.
python3 -m memory_bridge doctor

# Kontrollera kärnans exempelkonfiguration; okända inställningar avvisas.
python3 -m memory_bridge validate-config config/local.toml

# Alla tester. Fyra transporttester hoppas över om NBD-paketen saknas.
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
som återstår. Alla 47 tester passerar i [GitHub Actions](https://github.com/jojo-swe/memory-bridge/actions/runs/40181712375), inklusive NBD/TLS på loopback.

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
administratör. Ingen replikering, beständig kopia, återanslutning, automatisk
minnesåterlämning, GUI, Windows-integration eller publik minnespool finns ännu.
Data försvinner när givaren stängs eller sessionen avslutas. Använd syntetiska
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
