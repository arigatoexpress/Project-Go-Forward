import React from 'react';
import { HelpCircle, Phone, Calendar, MessageCircle } from 'lucide-react';
import ContentPage from '../components/ContentPage';
import {
  BUSINESS_NAME,
  BUSINESS_PHONE,
  BUSINESS_PHONE_RAW,
  BUSINESS_FULL_ADDRESS,
  SERVICE_AREA_CITIES,
} from '../constants';
import { DELIVERY_LINE, FINANCING_LINE, WARRANTY_LINE } from '../seoCopy';

const TOWN_LIST = SERVICE_AREA_CITIES.join(', ');

const FAQ_ITEMS = [
  {
    question: "What's the difference between a manufactured home, a mobile home, and a modular home?",
    answer:
      '"Mobile home" is the older term for factory-built homes made before June 15, 1976. '
      + 'Homes built after that date to the federal HUD Code are called manufactured homes. '
      + 'They meet national standards for construction, strength, energy efficiency, and fire safety. '
      + 'Modular homes are also factory-built but assembled to local or state building codes, like a site-built home. '
      + `${BUSINESS_NAME} sells manufactured homes in single-section and multi-section floor plans, including listed homes and orderable floorplans.`,
  },
  {
    question: 'Why do the listings say "Call for Price"?',
    answer:
      'If a home\'s price is Call for Price, the price is not posted online because the total depends on the options, delivery distance, and site setup for that home. '
      + `Call ${BUSINESS_PHONE} or request a quote on any home.`,
  },
  {
    question: 'How does financing work for a manufactured home?',
    answer: FINANCING_LINE,
  },
  {
    question: 'Do I need land to buy a home from you?',
    answer: 'Ask our team whether a home can go on land you own, family land, or a leased lot.',
  },
  {
    question: 'What does delivery and setup include?',
    answer: DELIVERY_LINE,
  },
  {
    question: 'What areas do you serve / deliver to?',
    answer:
      `${BUSINESS_NAME} is located at ${BUSINESS_FULL_ADDRESS}. Town pages cover ${TOWN_LIST}. ${DELIVERY_LINE}`,
  },
  {
    question: 'What warranty comes with a new manufactured home?',
    answer: WARRANTY_LINE,
  },
  {
    question: 'How long does the whole process take?',
    answer: 'Ask our team for a timeline for the home you are considering.',
  },
  {
    question: 'Can I tour homes in person?',
    answer:
      'Yes. Visit the Huffman showroom during posted showroom hours, or book a showroom visit. Some listings include photos and 3D tours.',
  },
  {
    question: 'How do I get started?',
    answer: `Call or text ${BUSINESS_PHONE}, request a price quote on any home, or book a showroom visit.`,
  },
];

const FAQ = ({ onBack }) => (
  <ContentPage
    title="Frequently Asked Questions"
    subtitle={`Questions about buying a manufactured home from ${BUSINESS_NAME}.`}
    onBack={onBack}
  >
    <div className="space-y-8">
      {FAQ_ITEMS.map((item) => (
        <section key={item.question} className="border-b border-gray-100 last:border-0 pb-6 last:pb-0">
          <h2 className="text-lg font-bold text-gray-900 mb-3 flex items-start gap-3">
            <HelpCircle size={22} className="text-blue-600 shrink-0 mt-0.5" />
            <span>{item.question}</span>
          </h2>
          <div className="pl-9 text-gray-700 leading-relaxed space-y-2">
            <p>{item.answer}</p>
          </div>
        </section>
      ))}

      <section className="bg-blue-50 rounded-xl p-6 border border-blue-100 mt-8">
        <h2 className="text-lg font-bold text-gray-900 mb-2">Still have questions?</h2>
        <p className="text-gray-700 mb-4">
          Call, book a visit, or start a chat.
        </p>
        <div className="flex flex-wrap items-center gap-3">
          <a
            href={`tel:${BUSINESS_PHONE_RAW}`}
            className="inline-flex items-center gap-2 bg-blue-600 text-white px-4 py-2 rounded-lg font-medium hover:bg-blue-700 transition"
          >
            <Phone size={18} />
            {BUSINESS_PHONE}
          </a>
          <a
            href="/appointments"
            className="inline-flex items-center gap-2 border border-blue-600 text-blue-700 px-4 py-2 rounded-lg font-medium hover:bg-blue-50 transition"
          >
            <Calendar size={18} />
            Book a Visit
          </a>
          <a
            href="/chat"
            className="inline-flex items-center gap-2 border border-blue-600 text-blue-700 px-4 py-2 rounded-lg font-medium hover:bg-blue-50 transition"
          >
            <MessageCircle size={18} />
            Chat with Tex
          </a>
        </div>
      </section>
    </div>
  </ContentPage>
);

export default FAQ;
