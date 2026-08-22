import React from 'react';
import { View, Text, StyleSheet } from 'react-native';
import { Card, Field, TextField, Button, InfoBanner } from '../../components/ui';
import { colors, type } from '../../theme';

function ContactCard({ title, badge, contact, onChange, errorPrefix, errors, onRemove, relationshipPlaceholder }) {
  return (
    <Card>
      <View style={styles.cardHeader}>
        <Text style={type.label}>{title}</Text>
        {badge ? (
          <View style={styles.badge}>
            <Text style={styles.badgeText}>{badge}</Text>
          </View>
        ) : onRemove ? (
          <Text style={styles.removeLink} onPress={onRemove}>
            Remove
          </Text>
        ) : null}
      </View>

      <Field label="Name" error={errors[`${errorPrefix}.name`]}>
        <TextField
          value={contact.name}
          onChangeText={(name) => onChange({ name })}
          placeholder="e.g. Sarah Wilson"
          autoCapitalize="words"
          error={!!errors[`${errorPrefix}.name`]}
        />
      </Field>
      <Field label="Relationship" error={errors[`${errorPrefix}.relationship`]}>
        <TextField
          value={contact.relationship}
          onChangeText={(relationship) => onChange({ relationship })}
          placeholder={relationshipPlaceholder}
          autoCapitalize="words"
          error={!!errors[`${errorPrefix}.relationship`]}
        />
      </Field>
      <Field label="Phone number" error={errors[`${errorPrefix}.phone`]} style={{ marginBottom: 0 }}>
        <TextField
          value={contact.phone}
          onChangeText={(phone) => onChange({ phone })}
          placeholder="0412 345 678"
          keyboardType="phone-pad"
          error={!!errors[`${errorPrefix}.phone`]}
        />
      </Field>
    </Card>
  );
}

export default function ContactsStep({ draft, setDraft, errors }) {
  const c = draft.contacts;
  const patch = (key, p) => setDraft({ contacts: { ...c, [key]: { ...c[key], ...p } } });

  return (
    <View>
      <InfoBanner>
        If the wearable detects something wrong, your next of kin is the first person our
        assistant calls. Please double-check this number.
      </InfoBanner>

      <ContactCard
        title="Next of kin"
        badge="Required"
        contact={c.nextOfKin}
        onChange={(p) => patch('nextOfKin', p)}
        errorPrefix="nextOfKin"
        errors={errors}
        relationshipPlaceholder="e.g. Daughter"
      />

      {c.secondary.enabled ? (
        <ContactCard
          title="Secondary contact"
          contact={c.secondary}
          onChange={(p) => patch('secondary', p)}
          errorPrefix="secondary"
          errors={errors}
          onRemove={() => patch('secondary', { enabled: false, name: '', relationship: '', phone: '' })}
          relationshipPlaceholder="e.g. Neighbour"
        />
      ) : (
        <Button
          title="+ Add a secondary contact (optional)"
          variant="secondary"
          onPress={() => patch('secondary', { enabled: true })}
          style={styles.addButton}
        />
      )}

      {c.gp.enabled ? (
        <ContactCard
          title="GP / care provider"
          contact={c.gp}
          onChange={(p) => patch('gp', p)}
          errorPrefix="gp"
          errors={errors}
          onRemove={() => patch('gp', { enabled: false, name: '', phone: '' })}
          relationshipPlaceholder="e.g. GP / care provider"
        />
      ) : (
        <Button
          title="+ Add GP / care provider (optional)"
          variant="secondary"
          onPress={() => patch('gp', { enabled: true })}
          style={styles.addButton}
        />
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  cardHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 14,
  },
  badge: {
    backgroundColor: colors.dangerSoft,
    borderRadius: 8,
    paddingHorizontal: 10,
    paddingVertical: 4,
  },
  badgeText: { color: colors.danger, fontSize: 12, fontWeight: '700' },
  removeLink: { color: colors.danger, fontSize: 15, fontWeight: '600', padding: 4 },
  addButton: { marginBottom: 14 },
});
