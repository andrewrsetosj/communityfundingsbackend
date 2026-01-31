'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { getCampaigns } from '@/lib/stripe';

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

export default function Home() {
  const [campaigns, setCampaigns] = useState<Campaign[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    async function fetchCampaigns() {
      try {
        const data = await getCampaigns();
        setCampaigns(data);
      } catch (error) {
        console.error('Failed to fetch campaigns:', error);
      } finally {
        setLoading(false);
      }
    }
    fetchCampaigns();
  }, []);

  if (loading) {
    return (
      <div className="flex min-h-[60vh] items-center justify-center">
        <div className="h-8 w-8 animate-spin rounded-full border-4 border-[#8BC34A] border-t-transparent" />
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-7xl px-4 py-12">
      <div className="mb-10">
        <h1 className="text-3xl font-bold text-gray-900">Active Campaigns</h1>
        <p className="mt-2 text-gray-600">Support community projects that matter</p>
      </div>

      <div className="grid gap-8 md:grid-cols-2 lg:grid-cols-3">
        {campaigns.map((campaign) => (
          <Link
            key={campaign.id}
            href={`/campaign/${campaign.id}`}
            className="group overflow-hidden rounded-xl border border-gray-100 bg-white shadow-sm transition-all hover:shadow-lg"
          >
            <div className="aspect-video overflow-hidden bg-gray-100">
              {campaign.image_url && (
                <img
                  src={campaign.image_url}
                  alt={campaign.title}
                  className="h-full w-full object-cover transition-transform group-hover:scale-105"
                />
              )}
            </div>
            <div className="p-5">
              <h3 className="font-semibold text-gray-900 group-hover:text-[#8BC34A]">
                {campaign.title}
              </h3>
              <p className="mt-2 line-clamp-2 text-sm text-gray-600">
                {campaign.description}
              </p>
              
              {/* Progress Bar */}
              <div className="mt-4">
                <div className="h-2 overflow-hidden rounded-full bg-gray-100">
                  <div
                    className="h-full rounded-full bg-[#8BC34A] transition-all"
                    style={{ width: `${Math.min(campaign.funding_percentage, 100)}%` }}
                  />
                </div>
                <div className="mt-2 flex items-center justify-between text-sm">
                  <span className="font-semibold text-[#8BC34A]">
                    {campaign.funding_percentage}% Funded
                  </span>
                  <span className="text-gray-500">
                    ${campaign.raised_amount.toLocaleString()} raised
                  </span>
                </div>
              </div>
              
              <div className="mt-3 text-xs text-gray-500">
                By {campaign.creator_name} • {campaign.donors_count} donors
              </div>
            </div>
          </Link>
        ))}
      </div>

      {campaigns.length === 0 && (
        <div className="rounded-xl border border-dashed border-gray-300 bg-gray-50 py-16 text-center">
          <p className="text-gray-500">No active campaigns yet.</p>
          <button className="mt-4 rounded-lg bg-[#8BC34A] px-6 py-2 text-white hover:bg-[#7CB342]">
            Start a Campaign
          </button>
        </div>
      )}
    </div>
  );
}
