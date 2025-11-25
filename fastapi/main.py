from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional, Dict, Any
import httpx
import os
from datetime import datetime

app = FastAPI(
    title="Omnichannel Integration API",
    description="FastAPI service integrating Receevi, Odoo, and SMS providers",
    version="1.0.0"
)

# Configuration
RECEEVI_URL = os.getenv("RECEEVI_URL", "http://receevi-web:3001")
RECEEVI_API_KEY = os.getenv("RECEEVI_API_KEY", "")
ODOO_URL = os.getenv("ODOO_URL", "http://odoo:3000")
ODOO_DB = os.getenv("ODOO_DB", "odoo")
ODOO_USERNAME = os.getenv("ODOO_USERNAME", "admin")
ODOO_PASSWORD = os.getenv("ODOO_PASSWORD", "admin")

# Pydantic Models
class ReceeviWebhook(BaseModel):
    """Receevi webhook payload"""
    event: str
    account: Optional[Dict[str, Any]] = None
    conversation: Optional[Dict[str, Any]] = None
    message: Optional[Dict[str, Any]] = None
    sender: Optional[Dict[str, Any]] = None

class OdooTicket(BaseModel):
    """Odoo helpdesk ticket"""
    name: str
    description: str
    partner_name: Optional[str] = None
    partner_email: Optional[str] = None
    partner_phone: Optional[str] = None
    channel: str = "chat"

class SMSMessage(BaseModel):
    """SMS message"""
    to: str
    message: str
    from_number: Optional[str] = None


@app.get("/")
async def root():
    """Health check endpoint"""
    return {
        "status": "running",
        "service": "Omnichannel Integration API",
        "timestamp": datetime.now().isoformat()
    }


@app.get("/health")
async def health_check():
    """Detailed health check"""
    health_status = {
        "api": "healthy",
        "receevi": "unknown",
        "odoo": "unknown",
        "timestamp": datetime.now().isoformat()
    }
    
    # Check Receevi
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{RECEEVI_URL}/api")
            health_status["receevi"] = "healthy" if response.status_code == 200 else "unhealthy"
    except Exception as e:
        health_status["receevi"] = f"error: {str(e)}"
    
    # Check Odoo
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{ODOO_URL}/web/health")
            health_status["odoo"] = "healthy" if response.status_code == 200 else "unhealthy"
    except Exception as e:
        health_status["odoo"] = f"error: {str(e)}"
    
    return health_status


@app.post("/webhooks/receevi")
async def receevi_webhook(webhook: ReceeviWebhook):
    """
    Receive webhooks from Receevi and create tickets in Odoo
    """
    try:
        # Only process message_created events
        if webhook.event != "message_created":
            return {"status": "ignored", "reason": "Not a message_created event"}
        
        # Extract message details
        message = webhook.message or {}
        conversation = webhook.conversation or {}
        sender = webhook.sender or {}
        
        # Skip if message is from agent
        if message.get("message_type") == "outgoing":
            return {"status": "ignored", "reason": "Message from agent"}
        
        # Prepare ticket data
        ticket_data = {
            "name": f"Chat from {sender.get('name', 'Unknown')} - Conv #{conversation.get('id')}",
            "description": message.get("content", ""),
            "partner_name": sender.get("name"),
            "partner_email": sender.get("email"),
            "partner_phone": sender.get("phone_number"),
            "channel": "chat",
            "receevi_conversation_id": conversation.get("id"),
            "receevi_account_id": webhook.account.get("id") if webhook.account else None
        }
        
        # Create ticket in Odoo (you'll implement this based on your Odoo setup)
        ticket_result = await create_odoo_ticket(ticket_data)
        
        return {
            "status": "success",
            "ticket_id": ticket_result.get("id"),
            "message": "Ticket created successfully"
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/webhooks/sms/incoming")
async def receive_sms(request: Request):
    """
    Receive incoming SMS from providers (Twilio, Africa's Talking, etc.)
    """
    try:
        body = await request.form()
        
        # Extract SMS details (adjust based on your provider)
        from_number = body.get("From") or body.get("from")
        message_body = body.get("Body") or body.get("text")
        to_number = body.get("To") or body.get("to")
        
        if not from_number or not message_body:
            raise HTTPException(status_code=400, detail="Missing required fields")
        
        # Create conversation in Receevi
        conversation_result = await create_receevi_conversation({
            "source_id": from_number,
            "inbox_id": os.getenv("RECEEVI_SMS_INBOX_ID"),
            "contact_inbox": {
                "source_id": from_number
            }
        })
        
        # Send message to conversation
        if conversation_result.get("id"):
            await send_receevi_message(
                conversation_id=conversation_result["id"],
                content=message_body
            )
        
        # Also create ticket in Odoo
        ticket_data = {
            "name": f"SMS from {from_number}",
            "description": message_body,
            "partner_phone": from_number,
            "channel": "sms"
        }
        
        await create_odoo_ticket(ticket_data)
        
        return {"status": "success", "message": "SMS processed"}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/sms/send")
async def send_sms(sms: SMSMessage):
    """
    Send SMS via Twilio or other provider
    """
    try:
        # This is a placeholder - implement based on your SMS provider
        # Example for Twilio:
        twilio_sid = os.getenv("TWILIO_ACCOUNT_SID")
        twilio_token = os.getenv("TWILIO_AUTH_TOKEN")
        twilio_from = os.getenv("TWILIO_FROM_NUMBER")
        
        if not all([twilio_sid, twilio_token, twilio_from]):
            raise HTTPException(
                status_code=500, 
                detail="Twilio credentials not configured"
            )
        
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"https://api.twilio.com/2010-04-01/Accounts/{twilio_sid}/Messages.json",
                auth=(twilio_sid, twilio_token),
                data={
                    "From": sms.from_number or twilio_from,
                    "To": sms.to,
                    "Body": sms.message
                }
            )
            
            if response.status_code == 201:
                return {"status": "success", "sid": response.json().get("sid")}
            else:
                raise HTTPException(
                    status_code=response.status_code,
                    detail=response.text
                )
                
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/odoo/tickets")
async def create_ticket(ticket: OdooTicket):
    """
    Create a helpdesk ticket in Odoo
    """
    try:
        result = await create_odoo_ticket(ticket.dict())
        return {"status": "success", "ticket": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# Helper Functions
async def create_odoo_ticket(ticket_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Create a ticket in Odoo via XML-RPC or REST API
    """
    # TODO: Implement Odoo API integration
    # This is a placeholder - you'll need to implement based on your Odoo setup
    # You can use odoo-rpc-client or make direct XML-RPC calls
    
    return {
        "id": "placeholder_ticket_id",
        "name": ticket_data.get("name"),
        "status": "created"
    }


async def create_receevi_conversation(conversation_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Create a conversation in Receevi
    """
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{RECEEVI_URL}/api/v1/accounts/{conversation_data.get('account_id')}/conversations",
                headers={"api_access_token": RECEEVI_API_KEY},
                json=conversation_data
            )
            
            if response.status_code in [200, 201]:
                return response.json()
            else:
                return {"error": response.text}
                
    except Exception as e:
        return {"error": str(e)}


async def send_receevi_message(conversation_id: int, content: str) -> Dict[str, Any]:
    """
    Send a message to a Receevi conversation
    """
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{RECEEVI_URL}/api/v1/accounts/{os.getenv('RECEEVI_ACCOUNT_ID')}/conversations/{conversation_id}/messages",
                headers={"api_access_token": RECEEVI_API_KEY},
                json={
                    "content": content,
                    "message_type": "incoming"
                }
            )
            
            if response.status_code in [200, 201]:
                return response.json()
            else:
                return {"error": response.text}
                
    except Exception as e:
        return {"error": str(e)}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
