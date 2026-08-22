import React from 'react';
import { View, Text, StyleSheet } from 'react-native';
import { Card, Field, ChoiceChips, SwitchRow, InfoBanner } from '../../components/ui';
import { SHARE_OPTIONS } from '../../model/profile';
import { type } from '../../theme';

const WHAT_IT_MEANS = [
  'The wearable watches for falls and for stillness that is unusual against this baseline.',
  'If something looks wrong, an assistant places a phone call to your next of kin.',
  'In this prototype an ambulance is never called automatically — that escalation step is recorded, not dialled.',
  'Location safe-zones and heart-rate checks may be added later. Each will ask for consent separately before turning on.',
];

export default function ConsentStep({ draft, setDraft, errors }) {
  const c = draft.consent;
  const patch = (p) => setDraft({ consent: { ...c, ...p } });

  return (
    <View>
      <Card>
        <Text style={[type.label, { marginBottom: 10 }]}>What monitoring means</Text>
        {WHAT_IT_MEANS.map((line) => (
          <View key={line} style={styles.bulletRow}>
            <Text style={styles.bullet}>•</Text>
            <Text style={[type.small, { fontSize: 15, flex: 1 }]}>{line}</Text>
          </View>
        ))}
      </Card>

      <Card>
        <SwitchRow
          label="I consent to this monitoring"
          sublabel="Required to use the wearable. You can withdraw at any time."
          value={c.monitoringConsent}
          onValueChange={(monitoringConsent) => patch({ monitoringConsent })}
        />
      </Card>
      {errors.monitoringConsent ? <InfoBanner tone="danger">{errors.monitoringConsent}</InfoBanner> : null}

      <Field
        label="Who can view this profile and any alerts?"
        hint="Select everyone who should have access."
      >
        <ChoiceChips
          multi
          options={SHARE_OPTIONS}
          value={c.sharedWith}
          onChange={(sharedWith) => patch({ sharedWith })}
        />
      </Field>
    </View>
  );
}

const styles = StyleSheet.create({
  bulletRow: { flexDirection: 'row', gap: 8, marginBottom: 8 },
  bullet: { fontSize: 15, lineHeight: 20 },
});
