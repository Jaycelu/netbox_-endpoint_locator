from django import forms


class EndpointLookupForm(forms.Form):
    q = forms.CharField(
        required=True,
        label="IP 或 MAC",
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "例如 10.128.129.241 或 aa:bb:cc:dd:ee:ff",
            }
        ),
    )