'use client';

import { useEffect, useState } from 'react';
import { loadStripe } from '@stripe/stripe-js';
import {
  Elements,
  CardNumberElement,
  CardExpiryElement,
  CardCvcElement,
  useStripe,
  useElements,
} from '@stripe/react-stripe-js';
import { createSetupIntent, getPaymentMethods, deletePaymentMethod, API_URL } from '@/lib/stripe';

const stripePromise = loadStripe(
  process.env.NEXT_PUBLIC_STRIPE_PUBLISHABLE_KEY || 'pk_test_REPLACE_WITH_YOUR_KEY'
);

type TabType = 'account' | 'profile' | 'payment';

interface PaymentMethod {
  id: string;
  brand: string;
  last4: string;
  exp_month: number;
  exp_year: number;
}

const elementStyle = {
  base: {
    fontSize: '16px',
    color: '#374151',
    fontFamily: 'Inter, system-ui, sans-serif',
    '::placeholder': {
      color: '#9CA3AF',
    },
  },
  invalid: {
    color: '#EF4444',
  },
};

// Payment Methods Form Component
function PaymentMethodsForm() {
  const stripe = useStripe();
  const elements = useElements();
  
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [savedCards, setSavedCards] = useState<PaymentMethod[]>([]);
  const [loadingCards, setLoadingCards] = useState(true);
  
  // Form state
  const [nameOnCard, setNameOnCard] = useState('Thomas.D');
  const [billingName, setBillingName] = useState('Thomas.D');
  const [billingAddress, setBillingAddress] = useState('4085 Gleason Drives');
  const [city, setCity] = useState('West Derickshire');
  const [zip, setZip] = useState('50703');
  const [country, setCountry] = useState('United States');

  // Fetch saved payment methods
  useEffect(() => {
    async function fetchPaymentMethods() {
      try {
        const data = await getPaymentMethods('user_001');
        setSavedCards(data.payment_methods || []);
      } catch (err) {
        console.error('Failed to fetch payment methods:', err);
      } finally {
        setLoadingCards(false);
      }
    }
    fetchPaymentMethods();
  }, []);

  const handleSaveCard = async (e: React.FormEvent) => {
    e.preventDefault();
    
    if (!stripe || !elements) return;
    
    setLoading(true);
    setError(null);
    setSuccess(null);

    try {
      // Create setup intent
      const { client_secret } = await createSetupIntent('user_001');

      // Confirm setup
      const cardNumberElement = elements.getElement(CardNumberElement);
      
      if (!cardNumberElement) {
        throw new Error('Card element not found');
      }

      const { error: stripeError, setupIntent } = await stripe.confirmCardSetup(
        client_secret,
        {
          payment_method: {
            card: cardNumberElement,
            billing_details: {
              name: nameOnCard,
              address: {
                line1: billingAddress,
                city: city,
                postal_code: zip,
                country: country === 'United States' ? 'US' : country,
              },
            },
          },
        }
      );

      if (stripeError) {
        setError(stripeError.message || 'Failed to save card');
      } else if (setupIntent?.status === 'succeeded') {
        setSuccess('Card saved successfully!');
        // Refresh payment methods
        const data = await getPaymentMethods('user_001');
        setSavedCards(data.payment_methods || []);
      }
    } catch (err: any) {
      setError(err.message || 'Something went wrong');
    } finally {
      setLoading(false);
    }
  };

  const handleDeleteCard = async (paymentMethodId: string) => {
    try {
      await deletePaymentMethod(paymentMethodId);
      setSavedCards(savedCards.filter(card => card.id !== paymentMethodId));
    } catch (err: any) {
      setError(err.message || 'Failed to delete card');
    }
  };

  return (
    <div>
      <h2 className="text-xl font-bold text-gray-900">Edit Payments Details</h2>
      
      {/* Saved Cards */}
      {savedCards.length > 0 && (
        <div className="mt-6 space-y-3">
          <h3 className="text-sm font-medium text-gray-700">Saved Cards</h3>
          {savedCards.map((card) => (
            <div
              key={card.id}
              className="flex items-center justify-between rounded-lg border border-gray-200 p-4"
            >
              <div className="flex items-center gap-3">
                <div className="flex h-10 w-16 items-center justify-center rounded bg-gray-100 text-xs font-medium uppercase text-gray-600">
                  {card.brand}
                </div>
                <div>
                  <p className="font-medium text-gray-900">•••• {card.last4}</p>
                  <p className="text-sm text-gray-500">
                    Expires {card.exp_month}/{card.exp_year}
                  </p>
                </div>
              </div>
              <button
                onClick={() => handleDeleteCard(card.id)}
                className="text-sm text-red-500 hover:text-red-600"
              >
                Remove
              </button>
            </div>
          ))}
        </div>
      )}

      <form onSubmit={handleSaveCard} className="mt-8 space-y-6">
        {/* Card Details */}
        <div className="grid gap-6 md:grid-cols-2">
          <div>
            <label className="block text-sm font-medium text-gray-700">Name on card</label>
            <div className="relative mt-1">
              <span className="absolute left-3 top-1/2 -translate-y-1/2">
                <svg className="h-5 w-5 text-[#8BC34A]" fill="currentColor" viewBox="0 0 20 20">
                  <rect x="2" y="4" width="16" height="12" rx="2" stroke="currentColor" strokeWidth="1.5" fill="none" />
                  <line x1="2" y1="8" x2="18" y2="8" stroke="currentColor" strokeWidth="1.5" />
                </svg>
              </span>
              <input
                type="text"
                value={nameOnCard}
                onChange={(e) => setNameOnCard(e.target.value)}
                className="w-full rounded-lg border border-gray-200 py-3 pl-10 pr-4 focus:border-[#8BC34A] focus:outline-none focus:ring-2 focus:ring-[#8BC34A]/20"
              />
            </div>
          </div>
          
          <div>
            <label className="block text-sm font-medium text-gray-700">Credit card number</label>
            <div className="relative mt-1 rounded-lg border border-gray-200 bg-white px-4 py-3">
              <CardNumberElement options={{ style: elementStyle, placeholder: 'Card number' }} />
              <span className="absolute right-3 top-1/2 -translate-y-1/2">
                <svg className="h-5 w-5 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z" />
                </svg>
              </span>
            </div>
          </div>
        </div>

        <div className="grid gap-6 md:grid-cols-3">
          <div>
            <label className="block text-sm font-medium text-gray-700">Security code</label>
            <input
              type="password"
              placeholder="******"
              className="mt-1 w-full rounded-lg border border-gray-200 py-3 px-4 focus:border-[#8BC34A] focus:outline-none focus:ring-2 focus:ring-[#8BC34A]/20"
              disabled
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700">Expiration date</label>
            <div className="mt-1 rounded-lg border border-gray-200 bg-white px-4 py-3">
              <CardExpiryElement options={{ style: elementStyle, placeholder: 'MM/YY' }} />
            </div>
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700">CVC</label>
            <div className="mt-1 rounded-lg border border-gray-200 bg-white px-4 py-3">
              <CardCvcElement options={{ style: elementStyle, placeholder: 'CVV' }} />
            </div>
          </div>
        </div>

        {/* Billing Address */}
        <div className="border-t border-gray-100 pt-6">
          <h3 className="text-lg font-semibold text-gray-900">Billing Address</h3>
          
          <div className="mt-6 grid gap-6 md:grid-cols-2">
            <div>
              <label className="block text-sm font-medium text-gray-700">Full name</label>
              <input
                type="text"
                value={billingName}
                onChange={(e) => setBillingName(e.target.value)}
                className="mt-1 w-full rounded-lg border border-gray-200 py-3 px-4 focus:border-[#8BC34A] focus:outline-none focus:ring-2 focus:ring-[#8BC34A]/20"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700">Billing address</label>
              <input
                type="text"
                value={billingAddress}
                onChange={(e) => setBillingAddress(e.target.value)}
                className="mt-1 w-full rounded-lg border border-gray-200 py-3 px-4 focus:border-[#8BC34A] focus:outline-none focus:ring-2 focus:ring-[#8BC34A]/20"
              />
            </div>
          </div>

          <div className="mt-6 grid gap-6 md:grid-cols-3">
            <div>
              <label className="block text-sm font-medium text-gray-700">City</label>
              <input
                type="text"
                value={city}
                onChange={(e) => setCity(e.target.value)}
                className="mt-1 w-full rounded-lg border border-gray-200 py-3 px-4 focus:border-[#8BC34A] focus:outline-none focus:ring-2 focus:ring-[#8BC34A]/20"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700">Zip</label>
              <input
                type="text"
                value={zip}
                onChange={(e) => setZip(e.target.value)}
                className="mt-1 w-full rounded-lg border border-gray-200 py-3 px-4 focus:border-[#8BC34A] focus:outline-none focus:ring-2 focus:ring-[#8BC34A]/20"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700">Country</label>
              <select
                value={country}
                onChange={(e) => setCountry(e.target.value)}
                className="mt-1 w-full rounded-lg border border-gray-200 py-3 px-4 focus:border-[#8BC34A] focus:outline-none focus:ring-2 focus:ring-[#8BC34A]/20"
              >
                <option>United States</option>
                <option>United Arab Emirates</option>
                <option>United Kingdom</option>
                <option>Canada</option>
              </select>
            </div>
          </div>
        </div>

        {error && (
          <div className="rounded-lg bg-red-50 p-3 text-sm text-red-600">{error}</div>
        )}
        
        {success && (
          <div className="rounded-lg bg-green-50 p-3 text-sm text-green-600">{success}</div>
        )}

        {/* Actions */}
        <div className="flex justify-end gap-4 pt-6">
          <button type="button" className="px-6 py-3 text-gray-600 hover:text-gray-900">
            View Profile
          </button>
          <button
            type="submit"
            disabled={!stripe || loading}
            className="rounded-lg bg-[#8BC34A] px-8 py-3 font-semibold text-white transition-colors hover:bg-[#7CB342] disabled:cursor-not-allowed disabled:opacity-50"
          >
            {loading ? 'Saving...' : 'Save'}
          </button>
        </div>
      </form>
    </div>
  );
}

// Main Settings Page
export default function SettingsPage() {
  const [activeTab, setActiveTab] = useState<TabType>('payment');

  const tabs = [
    { id: 'account' as TabType, label: 'Account' },
    { id: 'profile' as TabType, label: 'Edit Profile' },
    { id: 'payment' as TabType, label: 'Payment Methods' },
  ];

  return (
    <div>
      {/* Green Header */}
      <div className="bg-[#E8F5E9] py-12">
        <div className="mx-auto max-w-5xl px-4">
          <h1 className="text-3xl font-bold text-gray-900">Settings</h1>
        </div>
      </div>

      {/* Tabs */}
      <div className="border-b border-gray-200">
        <div className="mx-auto max-w-5xl px-4">
          <nav className="-mb-px flex gap-8">
            {tabs.map((tab) => (
              <button
                key={tab.id}
                onClick={() => setActiveTab(tab.id)}
                className={`border-b-2 py-4 text-sm font-medium transition-colors ${
                  activeTab === tab.id
                    ? 'border-[#8BC34A] text-gray-900'
                    : 'border-transparent text-gray-500 hover:border-gray-300 hover:text-gray-700'
                }`}
              >
                {tab.label}
              </button>
            ))}
          </nav>
        </div>
      </div>

      {/* Content */}
      <div className="mx-auto max-w-5xl px-4 py-10">
        {activeTab === 'payment' && (
          <Elements stripe={stripePromise}>
            <PaymentMethodsForm />
          </Elements>
        )}

        {activeTab === 'account' && (
          <div className="py-10 text-center text-gray-500">
            Account settings coming soon...
          </div>
        )}

        {activeTab === 'profile' && (
          <div className="py-10 text-center text-gray-500">
            Profile editing coming soon...
          </div>
        )}
      </div>
    </div>
  );
}
