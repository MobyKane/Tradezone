from django.shortcuts import render

# Create your views here.
def home_view(request):
    return render(request, 'home.html')

def list_item_view(request):
    return render(request, 'list_item.html')

def product_detail_view(request):
    return render(request, 'product_detail.html')

def checkout_view(request):
    return render(request, 'checkout.html')
