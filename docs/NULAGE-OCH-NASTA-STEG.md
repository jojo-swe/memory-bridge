# Memory Bridge – nuläge och nästa arbetspass

Sparat 2026-10-26. Utgångspunkt: version 0.11.0 (PR #4).

## Slutsats

Swap-experimentet är genomfört i en disponibel VM med förregistrerade
kriterier. **Användarnytta uteblev.** Memory Bridge som swap gav 88 % färre
slutförda begäranden (W2) och 9,3× längre jobbtid (W1, xz) än en vanlig lokal
swapfil under samma minnesbrist, med 0 datafel. Flaskhalsen är
användarrymdsvägen per sida (FUSE → NBD → Python): redan utan givare (P)
är den 4,5–4,9× långsammare än kärnans swap. 1 ms nätfördröjning gör den
ytterligare 5× långsammare. När adaptern själv står under samma minnestryck
hänger systemet sig (2 av 2; tolkat som rekursiv swap-deadlock). Se
[SWAP-EXPERIMENT-RESULTAT.md](SWAP-EXPERIMENT-RESULTAT.md).

## Vad som finns och har verifierats

Allt från 0.10 samt: låntagarens blockadapter (`memory-bridge export`),
O_DIRECT-kopia (temporär eller namngiven/omstartbar), flush-/FUA-semantik
som bara lovar vad lokal kopia ger, en gemensam deadline per begäran,
minneslåsning med MCL_ONFAULT, givarfördröjning för labb, experiment-
harness med felinjektion och rådata. 191 tester i CI utan överhoppade.

Felinjektion mot swap: fryst givare/tappat nät kostar en deadline (2 s),
död givare 0,3 s; därefter lokal väg; 0 datafel. Dödad adapter ger synligt
processfel (SIGSEGV), ingen tyst korruption.

## Beslutsgrinden är passerad – med negativt utfall

Kriteriedokumentet sade: fortsätt mot konsumentprodukt bara om nytta visas.
Den visades inte. Enligt samma dokument är nästa steg att analysera var
kostnaden ligger och pröva ändrad arkitektur eller smalare målgrupp – inte
GUI, parkoppling eller fler plattformar.

## Var kostnaden ligger (mätt)

1. Per-sida-kostnad i användarrymden: ≈ 90 s CPU per ≈ 100 s W2-körning (R),
   1 175 s CPU per xz-jobb. Python per 4 KiB/128 KiB-begäran, fyra kopior
   per skrivning, TLS, SHA-256, två processgränser (nbdfuse, worker).
2. Lokal kopia krävs alltid: disken avlastas aldrig, så swap-trafiken mot
   disk är lika stor som för L, plus nätet.
3. Givaren låser lika mycket RAM som hela swapytan (1 GiB för 1 GiB),
   oavsett hur mycket som används.
4. Deadlock när adaptern delar minnestryck med arbetslasten.

## Möjliga vägar (ej beslutade)

- **Kärnnära väg**: kärnans `nbd`-modul (ingen FUSE/loop) med en adapter i
  C/Rust utan allokering i I/O-vägen och PR_SET_IO_FLUSHER. Skulle ta bort
  kostnad 1 delvis, men inte 2–3. Mät P-läget först: om inte ens den lokala
  vägen når L:s hastighet är nätet ointressant.
- **Jämför med zram/zswap lokalt**: komprimerad swap i eget RAM är det
  etablerade sättet att "få mer RAM" utan ny hårdvara. Ett experiment med
  samma harness (zram som konfiguration Z) besvarar om Memory Bridge alls
  kan slå det billigaste alternativet.
- **Smalare mål**: inte swap utan explicit fjärrbuffert för ett program som
  själv hanterar förlust (t.ex. cache), där lokal kopia inte krävs.

## Konkret startpunkt nästa gång

Lägg till konfiguration Z (zram, samma budget) i `run_experiment.py` och
kör W2 fem omgångar. Om Z ≫ R, vilket är sannolikt, är frågan "varför
nätverk alls" den som måste besvaras innan mer kod skrivs.

## Fortsättningsinstruktion

> Läs detta dokument, SWAP-EXPERIMENT-RESULTAT.md och ARKITEKTUR.md.
> Utgå från mätdata, inte från antalet tester. Bygg inte funktioner ovanpå
> swap-vägen förrän P-läget (lokal väg utan givare) mäter i närheten av
> lokal swap.
