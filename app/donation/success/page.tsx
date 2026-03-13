'use client';

import { useSearchParams } from 'next/navigation';
import Link from 'next/link';

export default function DonationSuccessPage() {
  const searchParams = useSearchParams();
  const amount = searchParams.get('amount');
  const campaignTitle = searchParams.get('campaign');

  return (
    <div className="mx-auto max-w-lg px-4 py-20 text-center">
      {/* Success Icon */}
      <div className="mx-auto flex h-20 w-20 items-center justify-center rounded-full bg-[#8BC34A]/10">
        <svg
          className="h-10 w-10 text-[#8BC34A]"
          fill="none"
          stroke="currentColor"
          viewBox="0 0 24 24"
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth={2}
            d="M5 13l4 4L19 7"
          />
        </svg>
      </div>

      <h1 className="mt-6 text-3xl font-bold text-gray-900">Thank You!</h1>
      
      <p className="mt-4 text-lg text-gray-600">
        Your donation of{' '}
        <span className="font-semibold text-[#8BC34A]">${amount || '0'}</span>
        {campaignTitle && (
          <>
            {' '}to <span className="font-semibold">{campaignTitle}</span>
          </>
        )}{' '}
        has been processed successfully.
      </p>

      <p className="mt-4 text-gray-500">
        A confirmation email has been sent to your email address.
      </p>

      <div className="mt-8 flex flex-col gap-3 sm:flex-row sm:justify-center">
        <Link
          href="/"
          className="rounded-lg bg-[#8BC34A] px-6 py-3 font-semibold text-white transition-colors hover:bg-[#7CB342]"
        >
          Back to Campaigns
        </Link>
        <button className="rounded-lg border border-gray-200 px-6 py-3 font-semibold text-gray-700 transition-colors hover:bg-gray-50">
          Share on Social
        </button>
      </div>

      {/* Receipt Note */}
      <div className="mt-10 rounded-lg bg-gray-50 p-4 text-sm text-gray-600">
        <p>
          <strong>Note:</strong> This donation may be tax-deductible. Please keep this
          confirmation for your records.
        </p>
      </div>
    </div>
  );
}
