'use client';

import { useEffect, useState } from 'react';
import { useParams, useSearchParams, useRouter } from 'next/navigation';
import { loadStripe } from '@stripe/stripe-js';
import {
  Elements,
  CardNumberElement,
  CardExpiryElement,
  CardCvcElement,
  useStripe,
  useElements,
} from '@stripe/react-stripe-js';
import { getCampaign, createPaymentIntent, API_URL } from '@/lib/stripe';

// Load Stripe
const stripePromise = loadStripe(
  process.env.NEXT_PUBLIC_STRIPE_PUBLISHABLE_KEY || 'pk_test_REPLACE_WITH_YOUR_KEY'
);

interface Campaign {
  id: string;
  title: string;
  description: string;
  goal_amount: number;
  raised_amount: number;
  image_url: string;
  creator_name: string;
  funding_percentage: number;
}

// Stripe Element Styles
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

// Payment Form Component
function CheckoutForm({ campaign, amount }: { campaign: Campaign; amount: number }) {
  const stripe = useStripe();
  const elements = useElements();
  const router = useRouter();
  
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [country, setCountry] = useState('USA');
  const [rememberMe, setRememberMe] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    
    if (!stripe || !elements) {
      return;
    }

    setLoading(true);
    setError(null);

    try {
      // Create payment intent
      const { client_secret } = await createPaymentIntent({
        campaign_id: campaign.id,
        amount: amount,
        donor_name: 'Anonymous',
      });

      // Confirm payment
      const cardNumberElement = elements.getElement(CardNumberElement);
      
      if (!cardNumberElement) {
        throw new Error('Card element not found');
      }

      const { error: stripeError, paymentIntent } = await stripe.confirmCardPayment(
        client_secret,
        {
          payment_method: {
            card: cardNumberElement,
          },
        }
      );

      if (stripeError) {
        setError(stripeError.message || 'Payment failed');
      } else if (paymentIntent?.status === 'succeeded') {
        router.push(`/donation/success?amount=${amount}&campaign=${campaign.title}`);
      }
    } catch (err: any) {
      setError(err.message || 'Something went wrong');
    } finally {
      setLoading(false);
    }
  };

  return (
    <form onSubmit={handleSubmit}>
      <h2 className="text-2xl font-bold text-gray-900">Payment Method</h2>
      
      <div className="mt-6 space-y-4">
        {/* Card Number */}
        <div>
          <label className="block text-sm font-medium text-gray-700">Card Number</label>
          <div className="mt-1 rounded-lg border border-gray-200 bg-white px-4 py-3">
            <CardNumberElement options={{ style: elementStyle, placeholder: '123-456-789' }} />
          </div>
        </div>

        {/* Expiry and CVC */}
        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className="block text-sm font-medium text-gray-700">Expiration Date</label>
            <div className="mt-1 rounded-lg border border-gray-200 bg-white px-4 py-3">
              <CardExpiryElement options={{ style: elementStyle, placeholder: 'MM/YY' }} />
            </div>
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700">CVC</label>
            <div className="mt-1 rounded-lg border border-gray-200 bg-white px-4 py-3">
              <CardCvcElement options={{ style: elementStyle, placeholder: 'cvc' }} />
            </div>
          </div>
        </div>

        {/* Country */}
        <div>
          <label className="block text-sm font-medium text-gray-700">Country</label>
          <select
            value={country}
            onChange={(e) => setCountry(e.target.value)}
            className="mt-1 w-full rounded-lg border border-gray-200 bg-white px-4 py-3 text-gray-700 focus:border-[#8BC34A] focus:outline-none focus:ring-2 focus:ring-[#8BC34A]/20"
          >
            <option value="USA">USA</option>
            <option value="UAE">UAE</option>
            <option value="UK">UK</option>
            <option value="Canada">Canada</option>
            <option value="Other">Other</option>
          </select>
        </div>

        {/* Remember Me */}
        <div className="flex items-center gap-2">
          <input
            type="checkbox"
            id="remember"
            checked={rememberMe}
            onChange={(e) => setRememberMe(e.target.checked)}
            className="h-4 w-4 rounded border-gray-300 text-[#8BC34A] focus:ring-[#8BC34A]"
          />
          <label htmlFor="remember" className="text-sm text-gray-600">Remember Me</label>
        </div>

        <p className="text-xs text-gray-500">
          Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod tempor 
          incididunt ut labore et dolore magna aliqua. Ut enim ad minim veniam, quis nostrud exercitation
        </p>

        {error && (
          <div className="rounded-lg bg-red-50 p-3 text-sm text-red-600">
            {error}
          </div>
        )}

        <button
          type="submit"
          disabled={!stripe || loading}
          className="w-full rounded-lg bg-[#8BC34A] py-4 font-semibold text-white transition-colors hover:bg-[#7CB342] disabled:cursor-not-allowed disabled:opacity-50"
        >
          {loading ? (
            <span className="flex items-center justify-center gap-2">
              <svg className="h-5 w-5 animate-spin" viewBox="0 0 24 24">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
              </svg>
              Processing...
            </span>
          ) : (
            'Done'
          )}
        </button>
      </div>
    </form>
  );
}

// Main Checkout Page
export default function CheckoutPage() {
  const params = useParams();
  const searchParams = useSearchParams();
  const amount = Number(searchParams.get('amount')) || 200;
  
  const [campaign, setCampaign] = useState<Campaign | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    async function fetchCampaign() {
      try {
        const data = await getCampaign(params.id as string);
        setCampaign(data);
      } catch (error) {
        console.error('Failed to fetch campaign:', error);
      } finally {
        setLoading(false);
      }
    }
    fetchCampaign();
  }, [params.id]);

  if (loading) {
    return (
      <div className="flex min-h-[60vh] items-center justify-center">
        <div className="h-8 w-8 animate-spin rounded-full border-4 border-[#8BC34A] border-t-transparent" />
      </div>
    );
  }

  if (!campaign) {
    return (
      <div className="mx-auto max-w-3xl px-4 py-16 text-center">
        <h1 className="text-2xl font-bold text-gray-900">Campaign not found</h1>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-6xl px-4 py-12">
      <div className="grid gap-12 lg:grid-cols-2">
        {/* Left: Project Summary */}
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Project Summary</h1>
          
          <p className="mt-6 text-gray-600">
            {campaign.description}
          </p>

          {/* Project Card */}
          <div className="mt-8 flex gap-4 rounded-lg border border-gray-100 bg-white p-4">
            <div className="h-24 w-32 flex-shrink-0 overflow-hidden rounded-lg bg-gray-100">
              {campaign.image_url && (
                <img
                  src={campaign.image_url}
                  alt={campaign.title}
                  className="h-full w-full object-cover"
                />
              )}
            </div>
            <div>
              <h3 className="text-lg font-semibold text-gray-900">{campaign.title}</h3>
              <p className="mt-1 text-sm text-[#8BC34A]">{campaign.funding_percentage}% Funded</p>
              <p className="mt-1 text-sm text-gray-600">
                By <span className="font-medium text-[#8BC34A]">{campaign.creator_name}</span>
              </p>
            </div>
          </div>

          {/* Amount Summary */}
          <div className="mt-8 space-y-4 border-t border-gray-100 pt-6">
            <div className="flex items-center justify-between text-gray-600">
              <span>Your Reward:</span>
              <span className="font-medium text-gray-900">${amount.toFixed(2)}</span>
            </div>
            <div className="flex items-center justify-between border-t border-gray-100 pt-4">
              <span className="font-semibold text-gray-900">Total Amount</span>
              <span className="text-xl font-bold text-gray-900">${amount.toFixed(2)}</span>
            </div>
          </div>
        </div>

        {/* Right: Payment Form */}
        <div className="rounded-xl border border-gray-100 bg-white p-8 shadow-sm">
          <Elements stripe={stripePromise}>
            <CheckoutForm campaign={campaign} amount={amount} />
          </Elements>
        </div>
      </div>
    </div>
  );
}
