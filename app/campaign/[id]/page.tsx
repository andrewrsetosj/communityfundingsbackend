'use client';

import { useEffect, useState } from 'react';
import { useParams, useRouter } from 'next/navigation';
import Link from 'next/link';
import { getCampaign } from '@/lib/stripe';

interface Campaign {
  id: string;
  title: string;
  description: string;
  goal_amount: number;
  raised_amount: number;
  image_url: string;
  creator_name: string;
  funding_percentage: number;
  donors_count: number;
}

export default function CampaignPage() {
  const params = useParams();
  const router = useRouter();
  const [campaign, setCampaign] = useState<Campaign | null>(null);
  const [loading, setLoading] = useState(true);
  const [donationAmount, setDonationAmount] = useState(50);

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
        <Link href="/" className="mt-4 inline-block text-[#8BC34A] hover:underline">
          ← Back to campaigns
        </Link>
      </div>
    );
  }

  const predefinedAmounts = [25, 50, 100, 250, 500];

  return (
    <div className="mx-auto max-w-6xl px-4 py-12">
      <Link href="/" className="mb-6 inline-flex items-center text-sm text-gray-600 hover:text-gray-900">
        ← Back to campaigns
      </Link>

      <div className="grid gap-10 lg:grid-cols-3">
        {/* Main Content */}
        <div className="lg:col-span-2">
          <div className="aspect-video overflow-hidden rounded-xl bg-gray-100">
            {campaign.image_url && (
              <img
                src={campaign.image_url}
                alt={campaign.title}
                className="h-full w-full object-cover"
              />
            )}
          </div>

          <h1 className="mt-6 text-3xl font-bold text-gray-900">{campaign.title}</h1>
          
          <div className="mt-2 flex items-center gap-2 text-sm text-gray-600">
            <span>By</span>
            <span className="font-medium text-[#8BC34A]">{campaign.creator_name}</span>
          </div>

          <div className="mt-6">
            {/* Progress Bar */}
            <div className="h-3 overflow-hidden rounded-full bg-gray-100">
              <div
                className="h-full rounded-full bg-[#8BC34A] transition-all"
                style={{ width: `${Math.min(campaign.funding_percentage, 100)}%` }}
              />
            </div>
            
            <div className="mt-4 grid grid-cols-3 gap-4 text-center">
              <div>
                <div className="text-2xl font-bold text-[#8BC34A]">
                  ${campaign.raised_amount.toLocaleString()}
                </div>
                <div className="text-sm text-gray-500">raised</div>
              </div>
              <div>
                <div className="text-2xl font-bold text-gray-900">
                  {campaign.funding_percentage}%
                </div>
                <div className="text-sm text-gray-500">funded</div>
              </div>
              <div>
                <div className="text-2xl font-bold text-gray-900">
                  {campaign.donors_count}
                </div>
                <div className="text-sm text-gray-500">donors</div>
              </div>
            </div>
          </div>

          <div className="mt-8">
            <h2 className="text-xl font-semibold text-gray-900">About this project</h2>
            <p className="mt-4 whitespace-pre-wrap text-gray-600">{campaign.description}</p>
          </div>
        </div>

        {/* Donation Sidebar */}
        <div className="lg:col-span-1">
          <div className="sticky top-6 rounded-xl border border-gray-100 bg-white p-6 shadow-sm">
            <h3 className="text-lg font-semibold text-gray-900">Support this project</h3>
            
            <div className="mt-4 grid grid-cols-3 gap-2">
              {predefinedAmounts.map((amount) => (
                <button
                  key={amount}
                  onClick={() => setDonationAmount(amount)}
                  className={`rounded-lg border py-2 text-sm font-medium transition-all ${
                    donationAmount === amount
                      ? 'border-[#8BC34A] bg-[#8BC34A]/10 text-[#8BC34A]'
                      : 'border-gray-200 text-gray-700 hover:border-gray-300'
                  }`}
                >
                  ${amount}
                </button>
              ))}
            </div>

            <div className="mt-4">
              <label className="text-sm font-medium text-gray-700">Custom amount</label>
              <div className="relative mt-1">
                <span className="absolute left-4 top-1/2 -translate-y-1/2 text-gray-500">$</span>
                <input
                  type="number"
                  value={donationAmount}
                  onChange={(e) => setDonationAmount(Number(e.target.value))}
                  className="w-full rounded-lg border border-gray-200 py-3 pl-8 pr-4 focus:border-[#8BC34A] focus:outline-none focus:ring-2 focus:ring-[#8BC34A]/20"
                  min={1}
                />
              </div>
            </div>

            <button
              onClick={() => router.push(`/checkout/${campaign.id}?amount=${donationAmount}`)}
              className="mt-6 w-full rounded-lg bg-[#8BC34A] py-3 font-semibold text-white transition-colors hover:bg-[#7CB342]"
            >
              Donate ${donationAmount}
            </button>

            <p className="mt-4 text-center text-xs text-gray-500">
              Secure payment powered by Stripe
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}
