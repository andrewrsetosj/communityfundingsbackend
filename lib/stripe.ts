import { loadStripe, Stripe } from '@stripe/stripe-js';

let stripePromise: Promise<Stripe | null>;

export const getStripe = () => {
  if (!stripePromise) {
    stripePromise = loadStripe(
      process.env.NEXT_PUBLIC_STRIPE_PUBLISHABLE_KEY || 'pk_test_REPLACE_WITH_YOUR_KEY'
    );
  }
  return stripePromise;
};

// API Base URL
export const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

// API Helper Functions
export async function createCheckoutSession(data: {
  campaign_id: string;
  amount: number;
  donor_name?: string;
  donor_email?: string;
}) {
  const response = await fetch(`${API_URL}/api/stripe/create-checkout-session`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  
  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.detail || 'Failed to create checkout session');
  }
  
  return response.json();
}

export async function createPaymentIntent(data: {
  campaign_id: string;
  amount: number;
  donor_name?: string;
  donor_email?: string;
}) {
  const response = await fetch(`${API_URL}/api/stripe/create-payment-intent`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  
  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.detail || 'Failed to create payment intent');
  }
  
  return response.json();
}

export async function createSetupIntent(userId: string) {
  const response = await fetch(`${API_URL}/api/stripe/create-setup-intent?user_id=${userId}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
  });
  
  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.detail || 'Failed to create setup intent');
  }
  
  return response.json();
}

export async function getPaymentMethods(userId: string) {
  const response = await fetch(`${API_URL}/api/stripe/payment-methods/${userId}`);
  
  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.detail || 'Failed to get payment methods');
  }
  
  return response.json();
}

export async function deletePaymentMethod(paymentMethodId: string) {
  const response = await fetch(`${API_URL}/api/stripe/payment-methods/${paymentMethodId}`, {
    method: 'DELETE',
  });
  
  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.detail || 'Failed to delete payment method');
  }
  
  return response.json();
}

export async function getCampaign(campaignId: string) {
  const response = await fetch(`${API_URL}/api/campaigns/${campaignId}`);
  
  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.detail || 'Campaign not found');
  }
  
  return response.json();
}

export async function getCampaigns() {
  const response = await fetch(`${API_URL}/api/campaigns`);
  
  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.detail || 'Failed to get campaigns');
  }
  
  return response.json();
}
