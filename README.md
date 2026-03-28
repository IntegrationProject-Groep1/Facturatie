# Team Facturatie

### Over het team

Wij zijn het Facturatie‑team binnen het Integration Project. Onze verantwoordelijkheid is het automatisch verwerken, genereren en beheren van facturen. Wij zorgen ervoor dat inschrijvingen, consumpties, betalingen en annuleringen correct worden verwerkt in FossBilling, het facturatiesysteem van het project. Ons team staat centraal in de financiële afhandeling van het event: wij zorgen dat bedrijven en deelnemers correcte, tijdige en overzichtelijke facturen ontvangen.

### Wat ons systeem doet

Ons systeem luistert naar berichten die CRM doorstuurt via RabbitMQ. Op basis van deze berichten voeren wij de volgende taken uit:

**1. Verwerken van inschrijvingen**

Wanneer CRM een inschrijving doorstuurt, maken wij automatisch:
- een factuur voor bedrijven
- een bevestiging naar Mailing
  
**2. Verwerken van consumpties**

CRM stuurt consumptie‑informatie door die afkomstig is van de kassa.
Wij voegen deze items toe aan de openstaande factuur van het bedrijf.

**3. Verwerken van betalingen**

Wanneer CRM meldt dat een inschrijving aan de kassa is betaald:
- zetten wij de factuurstatus op betaald
- sturen wij een bevestiging naar Mailingµ
  
**4. Verwerken van annuleringen**

Bij annuleringen maken wij automatisch een creditnota aan.

**5. Automatische facturatie na het event**

Wanneer het event is afgelopen:
- sluiten wij alle openstaande facturen
- sturen wij ze door naar Mailing
- bedrijven ontvangen hun volledige factuur (inschrijvingen + consumpties)

**6. Monitoring & betrouwbaarheid**

Ons systeem stuurt:
- heartbeats 
- error logs naar de DLQ bij foutieve berichten
- meldingen wanneer het facturatiesysteem uitvalt
  
### Foutafhandeling

Wanneer een bericht ontbrekende velden heeft, niet aan de XSD voldoet, een onbekende enum bevat of niet verwerkt kan worden in FossBilling dan sturen wij het naar de Dead Letter Queue (DLQ). Het monitoringteam wordt automatisch verwittigd bij kritieke fouten.

### Waarom dit project belangrijk is

Ons systeem zorgt ervoor dat:
- bedrijven correcte facturen ontvangen
- deelnemers tijdig bevestigingen krijgen
- financiële gegevens niet verloren gaan
- het event administratief vlot verloopt
- alle teams op elkaar afgestemd blijven
  
Wij zijn de financiële backbone van het hele event.
