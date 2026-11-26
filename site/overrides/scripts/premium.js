var showPremium = false;
// A function, not a string: the site's Content-Security-Policy allows no eval.
setInterval(function () { showPremium = !showPremium; flashPremium(); }, 10000);

function flashPremium()
{
    element1 = $('id_premium_message_1');
    element2 = $('id_premium_message_2');

    if (showPremium)
    {
        if (element1 != null)
            element1.hide();
        if (element2 != null)
            element2.show();
    }
    else
    {
        if (element1 != null)
            element1.show();
        if (element2 != null)
            element2.hide();
    }
}
