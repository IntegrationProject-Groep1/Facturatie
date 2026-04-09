# Team Facturatie

### About the team

We are the Facturatie (Invoicing) team within the Integration Project. Our responsibility is to automatically process, generate, and manage invoices. We ensure that registrations, consumptions, payments, and cancellations are correctly handled in FossBilling, the invoicing system of the project. Our team is central to the financial processing of the event: we make sure that companies and participants receive correct, timely, and clear invoices.

### What our system does

Our system listens to messages forwarded by CRM via RabbitMQ. Based on these messages, we perform the following tasks:

**1. Processing registrations**

When CRM forwards a registration, we automatically create:
- an invoice for companies
- a confirmation to the Mailing team

**2. Processing consumptions**

CRM forwards consumption information originating from the point of sale.
We add these items to the open invoice of the company.

**3. Processing payments**

When CRM reports that a registration has been paid at the point of sale:
- we set the invoice status to paid
- we send a confirmation to the Mailing team

**4. Processing cancellations**

For cancellations, we automatically create a credit note.

**5. Automatic invoicing after the event**

When the event is over:
- we close all open invoices
- we forward them to the Mailing team
- companies receive their complete invoice (registrations + consumptions)

**6. Monitoring & reliability**

Our system sends:
- heartbeats
- error logs to the DLQ for invalid messages
- alerts when the invoicing system goes down

### Error handling

When a message has missing fields, does not comply with the XSD, contains an unknown enum value, or cannot be processed in FossBilling, we forward it to the Dead Letter Queue (DLQ). The monitoring team is automatically notified of critical errors.

### Why this project matters

Our system ensures that:
- companies receive correct invoices
- participants receive confirmations in time
- financial data is not lost
- the event runs smoothly from an administrative perspective
- all teams remain aligned

We are the financial backbone of the entire event.
