# Hackathon1 — Care Companion

First Hackathon experience — Hack the Gong 2026, aged care challenge track.

Companion app for a wearable that watches for signs of risk in elderly people living
alone. Signal sources (in build order): **falls / unusual immobility** (core), safe-zone
GPS wandering (stretch), vitals (stretch). When an anomaly is detected, an AI agent
places a real call to the next of kin; an "ambulance would be notified" escalation step
is logged, never dialled, in the prototype.

## Built so far — Module 1: Onboarding & baseline profile

A universal threshold ("no movement for 20 min = alert") can't work: normal varies per
person — the afternoon napper, the 10 am sleeper, someone with limited mobility. This
module produces the **personalized baseline** the anomaly engine will compare live
sensor data against.

Multi-step onboarding flow (welcome → demographics → emergency contacts → sleep →
weekly routine → hobbies & mobility → lifestyle → health context → consent → review)
that outputs a validated `UserBaselineProfile`, persisted locally. Partial progress
auto-saves, so a half-finished setup resumes after an app restart. A saved profile can
be re-opened and edited.

## Run it

```bash
npm install
npx expo start
```

Scan the QR code with the Expo Go app (iOS/Android). Everything used here (AsyncStorage,
safe-area) runs inside Expo Go — no native build needed.

## The data contract

Later modules read this object and nothing else:

```js
UserBaselineProfile {
  schemaVersion: 1,
  demographics: { name, sex, dob: 'YYYY-MM-DD', livingSituation },
  emergencyContacts: [{ name, relationship, phone, isPrimary }],  // primary first
  sleep: { typicalWake: 'HH:MM', typicalSleep: 'HH:MM', napPattern: string|null },
  weeklyRoutine: {
    monday: [{ activity, expectedTime: 'HH:MM', expectedDuration: minutes }],
    // … tuesday–sunday
  },
  hobbies: [string],
  mobilityLevel: 'fully_mobile' | 'walking_aid' | 'limited_mobility',
  lifestyle: { diet, smoking: { status, frequency }, alcohol: { status, frequency } },
  healthContext: [string],   // self-reported, advisory only — never diagnostic
  medicationCount: number | null,
  consent: { monitoringConsent: bool, sharedWith: ['nextOfKin' | 'carer' | 'gp'] },
  deviceId: null,            // reserved for wearable pairing
  completedAt: ISO datetime
}
```

The completed profile is visible in-app: **View raw profile JSON** on the home screen.

## Where things live

| Path | What it is |
| --- | --- |
| `src/model/profile.js` | Schema, option enums, validation, draft ⇄ profile mapping. Pure data logic — no UI, no sensors. |
| `src/storage/profileStore.js` | Persistence (AsyncStorage). Later modules call `loadProfile()`; swapping in Firebase/Supabase touches only this file. |
| `src/onboarding/OnboardingFlow.js` | Step controller: progress, validation, autosave, final build-and-save. |
| `src/onboarding/steps/` | One screen per onboarding section. |
| `src/screens/ProfileHomeScreen.js` | Post-setup summary + raw contract JSON viewer. |
| `src/components/ui.js` | Shared form controls (chips, tag editor, time fields…). |
| `onboarding questions/` | Original question brainstorm this flow was built from. |

## Intentionally not in this pass

BLE/wearable pairing (`deviceId` reserved), live sensor ingestion, anomaly-detection
logic, and the Twilio/AI call integration — all designed to consume the contract above
in the next build passes.

## Notes for the demo

- Health context is **self-reported and advisory only**: it gives the calling agent
  useful context ("they do have a heart condition"). The device never claims to detect
  or diagnose conditions.
- Consent to monitoring is required to finish onboarding; data visibility
  (next of kin / carer / GP) is chosen by the user.
